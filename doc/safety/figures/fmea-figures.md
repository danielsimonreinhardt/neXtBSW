<!-- SPDX-License-Identifier: Apache-2.0 -->

# Bilder zur Fehleranalyse (FMEA Ausgabe 2)

Ergänzende Darstellung **ohne inhaltliche Änderung** der [Fehleranalyse der Stufe-1-Mechanismen](../fmea.md),
Ausgabe 2 vom 2026-10-04 (DOC-55; Freigabe nach der Nachprüfung der Befunde aus CM-02 ausstehend, siehe Kopf der
FMEA). Maßgeblich ist immer die FMEA; bei einem Widerspruch gilt der Text dort, und das Bild ist ein Befund. In die
FMEA selbst kommen die Bilder erst mit ihrer nächsten Ausgabe.

R1 ist eine Entwicklungsfreigabe **ohne Sicherheitsanspruch**.

## F1: Blöcke → Fehlerarten → Mechanismen (Programmfluss, Speicher, Peripherie)

```mermaid
%%{init: {'theme':'base','flowchart':{'curve':'basis'},'themeVariables':{'fontFamily':'Segoe UI, Helvetica, Arial, sans-serif','fontSize':'14px','background':'#ffffff','primaryColor':'#ffffff','primaryBorderColor':'#64748b','primaryTextColor':'#0f172a','lineColor':'#475569','edgeLabelBackground':'#ffffff','tertiaryColor':'#f8fafc','clusterBkg':'#f8fafc','clusterBorder':'#cbd5e1'}}}%%
flowchart LR
    subgraph B31["3.1 program flow, time"]
        f01("safety task stops") --> SM01("SM-01")
        f02("watchdog reconfigured") --> SM02("SM-02")
        f03("service from wrong path") --> SM03("SM-03")
        f04("wrong flow, wrong period") --> SM04("SM-04")
        f05("cycle or runnable overrun") --> SM05("SM-05")
        f06("core halted over 10 ms") --> SM06("SM-06")
        f07("watchdog cannot reset") --> SM07("SM-07")
    end
    subgraph B32["3.2 memory"]
        f10("safety data overwritten") --> SM10("SM-10")
        f11("fusa gate state wrong") --> SM11("SM-11")
        f12("MPU off or incomplete") --> SM12("SM-12")
        f13("region descriptor changed") --> SM13("SM-13")
        f14("flash image corrupted") --> SM14("SM-14")
        f15("RAM ECC double-bit") --> SM15("SM-15")
        f16("flash ECC double-bit") --> SM16("SM-16")
        f55("ECC decoder latent fault") --> SM55("SM-55")
        f17("fault exception") --> SM17("SM-17")
    end
    subgraph B33["3.3 periphery, supply, registers"]
        f18("ADC reference drift") --> SM18("SM-18")
        f19("ADC path broken") --> SM19("SM-19")
        f20("internal supply out of range") --> SM20("SM-20")
        f21("locked pin config changed") --> SM21("SM-21")
        f22("safety register drift") --> SM22("SM-22")
    end

    classDef own fill:#ccfbf1,stroke:#0f766e,stroke-width:1.5px,color:#0f172a
    classDef grp fill:#f8fafc,stroke:#cbd5e1,rx:8,ry:8
    class SM01,SM02,SM03,SM04,SM05,SM06,SM07,SM10,SM11,SM12,SM13,SM14,SM15,SM16,SM55,SM17,SM18,SM19,SM20,SM21,SM22 own
    class B31,B32,B33 grp
```

## F2: Blöcke → Fehlerarten → Mechanismen (Ausgänge, Daten, Start)

```mermaid
%%{init: {'theme':'base','flowchart':{'curve':'basis'},'themeVariables':{'fontFamily':'Segoe UI, Helvetica, Arial, sans-serif','fontSize':'14px','background':'#ffffff','primaryColor':'#ffffff','primaryBorderColor':'#64748b','primaryTextColor':'#0f172a','lineColor':'#475569','edgeLabelBackground':'#ffffff','tertiaryColor':'#f8fafc','clusterBkg':'#f8fafc','clusterBorder':'#cbd5e1'}}}%%
flowchart LR
    subgraph B34["3.4 outputs, safe state"]
        f23("output stays energised") --> SM23("SM-23")
        f24("safe_off output without adapter") --> SM24("SM-24")
        f25("bridge switches until reset") --> SM25("SM-25")
    end
    subgraph B35["3.5 data, communication"]
        f30("invalid measurement used") --> SM30("SM-30")
        f31("CAN message corrupted, repeated, lost") --> SM31("SM-31")
        f32("sending into unhealthy bus") --> SM32("SM-32")
        f34("device fault not attributed") --> SM34("SM-34")
    end
    subgraph B36["3.6 start-up, reset, ECU state"]
        f50("restart reason unknown") --> SM50("SM-50")
        f51("image incomplete or foreign") --> SM51("SM-51")
        f53("run-down does not finish") --> SM53("SM-53")
    end

    classDef own fill:#ccfbf1,stroke:#0f766e,stroke-width:1.5px,color:#0f172a
    classDef grp fill:#f8fafc,stroke:#cbd5e1,rx:8,ry:8
    class SM23,SM24,SM25,SM30,SM31,SM32,SM34,SM50,SM51,SM53 own
    class B34,B35,B36 grp
```

**Legende zu F1 und F2**

- Ein Kasten je Block der FMEA (Abschnitte 3.1 bis 3.6, gegliedert wie Konzept 6.1 bis 6.7), eine Zeile je
  angerechnetem Mechanismus: 31 Mechanismen.
- Die Fehlerart ist auf wenige Wörter verkürzt nach dem Fehlermodell des Katalogs (Feld `FAULT_MODEL` in
  [`mechanisms.sdoc`](../../requirements/safety/mechanisms.sdoc)); was ein Mechanismus ausdrücklich **nicht** fängt,
  steht in der Zeile „Faults considered“ seines Blocks.
- Nicht dargestellt: die fünf nicht angerechneten Mechanismen SM-26, SM-27, SM-58, SM-59, SM-60 (analysiert, ohne
  Klasse) und die Stufe-2-Mechanismen der Bausteintreiber.

Quelle: FMEA Ausgabe 2, [Abschnitt 3](../fmea.md#3-per-mechanism-analysis) und
[Abschnitt 0](../fmea.md#0-result-in-one-page).

## F3: Abdeckungsübersicht

```mermaid
%%{init: {'theme':'base','flowchart':{'curve':'basis'},'themeVariables':{'fontFamily':'Segoe UI, Helvetica, Arial, sans-serif','fontSize':'14px','background':'#ffffff','primaryColor':'#ffffff','primaryBorderColor':'#64748b','primaryTextColor':'#0f172a','lineColor':'#475569','edgeLabelBackground':'#ffffff','tertiaryColor':'#f8fafc','clusterBkg':'#f8fafc','clusterBorder':'#cbd5e1'}}}%%
pie showData
    title Coverage class per analysed mechanism, 36 blocks
    "high" : 13
    "medium" : 12
    "low" : 1
    "low to medium" : 1
    "reaction" : 4
    "not claimed" : 5
```

**Legende**

- Zählung der Spalte „Coverage class“ der Übersichtstabelle, 36 Blöcke. Klassen sind ordinal, **keine Prozentwerte**;
  die FMEA macht ausdrücklich keine Zuordnung zu 60/90/99 %.
- high: SM-06, SM-07, SM-10, SM-12, SM-13, SM-14, SM-17, SM-19, SM-24, SM-31, SM-50, SM-51, SM-55.
  medium: SM-01, SM-02, SM-04, SM-05, SM-11, SM-15, SM-16, SM-18, SM-21, SM-22, SM-30, SM-32.
  low: SM-03. low to medium: SM-20. reaction: SM-23, SM-25, SM-34, SM-53. not claimed: SM-26, SM-27, SM-58, SM-59,
  SM-60.
- Eingeordnet nach dem ersten Wort der Zelle: SM-14 „high at start; at run time high with a re-check period …, low
  with period 0“ als high, SM-05 „medium / none (activation)“ als medium, SM-06 „high (blind spot …)“ als high.

Quelle: FMEA Ausgabe 2, [Abschnitt 3.7](../fmea.md#37-summary-table) und
[Abschnitt 1.3](../fmea.md#13-diagnostic-coverage-classes-not-percentages).

## F4: Urteile zu den angenommenen Sicherheitszielen

```mermaid
%%{init: {'theme':'base','flowchart':{'curve':'basis'},'themeVariables':{'fontFamily':'Segoe UI, Helvetica, Arial, sans-serif','fontSize':'14px','background':'#ffffff','primaryColor':'#ffffff','primaryBorderColor':'#64748b','primaryTextColor':'#0f172a','lineColor':'#475569','edgeLabelBackground':'#ffffff','tertiaryColor':'#f8fafc','clusterBkg':'#f8fafc','clusterBorder':'#cbd5e1'}}}%%
flowchart LR
    G1("ASG-01") --> V1("partial")
    G2("ASG-02") --> V2("covered with limits")
    G3("ASG-03") --> V3("not covered")
    G4("ASG-04") --> V4("not covered")
    G5("ASG-05") --> V5("covered for CAN data")
    G6("ASG-06") --> V6("covered with limits")
    G7("ASG-07") --> V7("partial")

    V1 -.- L1("G1, G2, F4, F9")
    V2 -.- L2("F12, F13, DF-05")
    V3 -.- L3("F16")
    V4 -.- L4("G4")
    V5 -.- L5("G3, AoU-11")
    V6 -.- L6("F9 ... F12, F14, F15")
    V7 -.- L7("N4")

    classDef ok fill:#dcfce7,stroke:#15803d,stroke-width:1.5px,color:#0f172a
    classDef part fill:#fef3c7,stroke:#b45309,stroke-width:1.5px,color:#0f172a
    classDef no fill:#fee2e2,stroke:#b91c1c,stroke-width:1.5px,color:#0f172a
    class V2,V5,V6 ok
    class V1,V7 part
    class V3,V4 no
```

**Legende**

- Farbe = Urteil: grün abgedeckt (mit Grenzen oder für CAN-Daten), gelb teilweise, rot nicht abgedeckt.
- Alle sieben Urteile des Konzepts sind **bestätigt**; die Analyse fügt Grenzen hinzu, ändert aber kein Urteil.
- Gestrichelt: die Befunde und Lücken, die Abschnitt 4 je Ziel nennt. Viele davon sind inzwischen geschlossen oder
  eingeengt (z. B. F9 durch SM-04 seit SYS-40, F11/F12/F14 seit SYS-41, F15 seit SYS-42); der Stand je Befund steht
  in der Zeile des Ziels.

Quelle: FMEA Ausgabe 2, [Abschnitt 4](../fmea.md#4-the-assumed-safety-goals-checked).

## F5: Zeitbegriffe je Mechanismus

```mermaid
%%{init: {'theme':'base','flowchart':{'curve':'basis'},'themeVariables':{'fontFamily':'Segoe UI, Helvetica, Arial, sans-serif','fontSize':'14px','background':'#ffffff','primaryColor':'#ffffff','primaryBorderColor':'#64748b','primaryTextColor':'#0f172a','lineColor':'#475569','edgeLabelBackground':'#ffffff','tertiaryColor':'#f8fafc','clusterBkg':'#f8fafc','clusterBorder':'#cbd5e1'}}}%%
flowchart LR
    F(["fault present"]) -- "detection time" --> E(["event raised"]) -- "reaction time" --> R(["reaction in effect<br/>S1: outputs off<br/>S2: MCU in reset"])
    SUM("minimum FTTI served<br/>= detection + reaction, worst case")
    FTT("FTT of the item, integrator")
    CMP(("FTT longer?<br/>AoU-05"))
    R --> SUM --> CMP
    FTT --> CMP

    classDef safety fill:#fee2e2,stroke:#b91c1c,stroke-width:1.5px,color:#0f172a
    class R safety
```

**Legende**

- Je Mechanismus nennt die FMEA Erkennungszeit, Reaktionszeit und daraus die kürzeste Fehlertoleranzzeit, der er
  dienen kann. Nur wenn die FTT des Integrators **länger** ist, darf er sich für ein Ziel auf den Mechanismus stützen.
- Zwei Formen: **nominal** (Demo-Konstanten, Safety-Task im Takt) und **garantiert** (was das Element selbst erkennt,
  wenn die Zeitannahme bricht), z. B. 10 ms nominal, 17 ms garantiert für die Prüfungen im Safety-Task.
- Die Neustartzeit nach S2 zählt nicht zur Zeit bis „stromlos“, ist aber die Zeit, in der die ECU ausfällt.

Quelle: FMEA Ausgabe 2, [Abschnitt 1.2](../fmea.md#12-time-terms) und
[Abschnitt 2](../fmea.md#2-the-timing-basis-corrected-f9).
