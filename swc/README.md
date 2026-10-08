# Software-Komponenten (Simulink, AUTOSAR Classic)

Anwendungslogik entsteht als Simulink-Modell, das als **AUTOSAR-Classic-Softwarekomponente (SWC)** mit dem
AUTOSAR Blockset konfiguriert ist. Embedded Coder erzeugt C-Code und ARXML. Eine eigene, schlanke RTE
(`tools/rtegen`) verbindet die Ports mit der Basis-Software (OpenBSW) und ruft die Runnables zyklisch auf.

A component can also be written in C by hand, with the same RTE API and without MATLAB: see
[Hand-written C components (RTE-20)](#hand-written-c-components-rte-20).

```
swc/<Swc>/<Swc>.slx ──(tools/swc/swc_build.m, MATLAB)──► swc/<Swc>/generated/{src,arxml}   (eingecheckt)
                                                                  │
projects/<p>/project.yaml → rte.yaml ─(tools/rtegen, CMake)───────┴─► build/<preset>/generated/rte
ecu/asyncCoreConfiguration/tasks.yaml ─┘                                 Rte_Type.h, Rte_<Swc>.h, Rte.c,
                                                                         RteConfig.h/.cpp, RteTasks.h,
                                                                         tasks/async/Config.h
ecu/application: RteSystem ── init runnables + rte::init(), dann je Anwendungs-Task in seinem Kontext alle period_ms:
                              readInputs(task) → fällige Runnables des Tasks in Reihenfolge → writeOutputs(task)
```

## Voraussetzungen

- MATLAB **R2024a** mit Simulink, Embedded Coder und AUTOSAR Blockset (nur zum Ändern eines Modells).
- Für den Firmware-Build ist MATLAB nicht nötig: Der erzeugte Code liegt im Repo.
- The published (open source) tree does **not** contain the code Simulink Coder generated (MATLAB student licence,
  decision F9 A, PLAT-119): there `swc/DemoSwc` is built from the hand-written C substitute
  [`swc/DemoSwcSubstitute`](DemoSwcSubstitute/README.md) with the same RTE interface; with your own MATLAB licence
  you generate the original from the model.

## Modellregeln

| Thema | Regel |
|---|---|
| Modelltyp | Export-Function-Modell, System Target File `autosar.tlc` |
| Runnables | je eine Function-Call-Inport pro Runnable, die Abtastzeit ist die Periode (ganze ms, Vielfaches der RTE-Basisperiode). Der Init-Runnable `<Swc>_Init` entsteht automatisch. |
| Ports | Root-Inports = Required Ports, Root-Outports = Provided Ports, Sender-Receiver, Zugriff `ExplicitReceive`/`ExplicitSend` |
| Datentypen | AUTOSAR-Plattformtypen (`boolean`, `uint8/16/32`, `sint8/16/32`, `float32/64`); arrays, records (buses), enumerations and fixed point between components, see "Data types (RTE-04)" |
| Hardware | keine Hardware-Blöcke (MBDT o. Ä.) im Modell: Ein-/Ausgänge nur über Ports |
| Zielhardware | `ARM Compatible->ARM Cortex-M` |

| Kalibrierparameter | Elemente des ParameterReceiver-Ports `CalPrm` (Abschnitt „Kalibrierparameter“), Werte nur in `<Swc>_data.m` |

Nicht unterstützt (noch): Client-Server-Ports, Mode-Management, mehrere Instanzen.

## Arbeitsablauf

1. **Neue Komponente:** `swc/template/swc.yaml` nach `swc/<Swc>/swc.yaml` kopieren, Name, Runnables und Ports
   eintragen, `python tools/swc/swc_yaml.py check swc/<Swc>`, dann
   `matlab -batch "addpath tools/swc; swc_create('swc/<Swc>')"`. Das legt `<Swc>.slx` mit Subsystemen je Runnable,
   Ports und AUTOSAR-Zuordnung an; danach ist die `.slx` die Quelle. Bausteine: Library Browser „neXtBSW
   Components“ (`swc/template/NxSwcLib.slx`), z. B. `SensorQualifier`.
2. **Ports einer bestehenden Komponente ändern** (TOOL-14): Die `.slx` wird nie neu erzeugt. Geänderte Ports der
   `swc.yaml` schreibt `swc_ports` gezielt ins bestehende Modell – hinzufügen, umbenennen, entfernen, Datentyp –,
   jeder andere Block und jede andere Linie bleibt, wo sie ist:
   ```matlab
   swc_ports('swc/<Swc>', 'report', 'ports.json')   % nur lesen: Ports und was im Modell daran hängt
   swc_ports('swc/<Swc>', 'apply',  'plan.json')    % schreiben; der Plan muss eine vorhandene Kopie der .slx nennen
   ```
   Aus dem Studio mit Vorschau und Sicherung: `studio component ports projects/<projekt> <Swc> [--detach] --apply`
   (tools/studio/README.md). Ein Port, an dem Logik hängt, wird **abgelehnt**, solange das Abtrennen nicht
   ausdrücklich erlaubt ist – und auch dann wird kein Block gelöscht: an die Stelle des Ports tritt
   `<Port>_Removed` (Constant bzw. Terminator). Runnables ändert man weiterhin in Simulink.
3. **Code erzeugen** (nach jeder Modelländerung):
   ```matlab
   cd <repo>; addpath tools/swc
   swc_build('swc/DemoSwc')
   ```
   Schreibt `generated/src`, `generated/arxml` und `generated/model.sha256` (Hash der `.slx`). Datumszeilen werden
   aus dem erzeugten Code entfernt, damit Git nur echte Änderungen zeigt. Passt die `.slx` nicht mehr zum
   Hash, warnt der CMake-Konfigurierlauf, dass der eingecheckte Code veraltet ist.
4. **Verbinden** in der ECU-Beschreibung des Projekts (`projects/<projekt>/rte.yaml`, in `project.yaml` unter
   `project.rte`). Paths inside it (`path`, `can.dbc`) are relative to that `rte.yaml` (RTE-22, `schema_version: 2`),
   not to the repository root: a reusable component like DemoSwc is reached with `../../swc/<Swc>`, a
   project-specific one with `swc/<Swc>`, and the project's DBC lies next to the file. That way a project keeps
   working when it is moved, copied or kept outside the neXtBSW tree. An older description without the key is
   format 1, whose paths were relative to the repository root (`rtegen --repo`); `nxconfig migrate` raises it
   (`projects/README.md`).
   ```yaml
   schema_version: 2
   components:
     - name: DemoSwc
       path: ../../swc/DemoSwc
   connections:
     DemoSwc.Button1.Button1: dio.in.BUTTON1          # Required Port <- digitaler Eingang
     DemoSwc.MotorNtc_mV.MotorNtc_mV: adc.mv.MOTOR_NTC  # Required Port <- ADC in mV
     DemoSwc.Led1.Led1: dio.out.LED1                  # Provided Port -> digitaler Ausgang
     DemoSwc.PcCommandQlf.PcCommandQlf: can.qlf.PC_Command  # Qualifier einer Empfangsbotschaft (uint8)
     DemoSwc.MotorNtcFiltered_mV.MotorNtcFiltered_mV: rte   # bleibt in der RTE
     # OtherSwc.In.Value: DemoSwc.Out.Value           # Komponente zu Komponente
   ```
   `rtegen` prüft: jeder Required Port verbunden, Richtung und Datentyp passen zum Ziel, Signal existiert im
   Board-Profil und wird vom Projekt genutzt (`use:`/`pwm:` in `project.yaml`), Perioden und Versätze passen zum Task
   (Abschnitt „Tasks“).
5. **Firmware bauen** wie gewohnt (`cmake --build --preset buildtest-s32k148-min`, `projects/README.md`), flashen per
   CAN oder J-Link.
6. **Am Target prüfen:** Konsole `rte signals` (alle Puffer mit Verbindung) und `rte runnables` (je Task Kontext,
   Periode, Zyklen, Überläufe, längster Zyklus; je Runnable Periode, Versatz, Aufrufe, Laufzeit), `stats cpu` und
   `stats stack` für die Tasks.

## Hand-written C components (RTE-20)

A component can also be written in C by hand instead of being modelled in Simulink. It is described, connected,
scheduled and calibrated exactly like a Simulink component; only the source of the code differs, and no MATLAB is
involved at any step.

```
swc/<Swc>/swc.yaml (implementation: c) ──(tools/swc/swc_skeleton.py create, once)──► swc/<Swc>/src/<Swc>.c  (yours)
        │                                                                                    │
        └──(tools/rtegen reads swc.yaml instead of ARXML)──► Rte_<Swc>.h, Rte.c, RteConfig.cpp ─┴─► ecu/application
```

| | Simulink component | C component |
|---|---|---|
| `swc.yaml` | optional; after `swc_create` only the description the model came from | **required and the master**: `implementation: c` |
| What rtegen reads | `generated/arxml/*.arxml` | `swc.yaml` (runnables, ports, parameters) |
| Code | `generated/src` (Embedded Coder, `swc_build`) | `src/*.c` (hand-written) |
| Runnable functions | `<Runnable>` (Embedded Coder), `<Swc>_Init` | **`<Swc>_<Runnable>`**, `<Swc>_Init` |
| Port and parameter API | `Rte_Read_<Port>_<Port>`, `Rte_Write_<Port>_<Port>`, `Rte_Prm_CalPrm_<Name>` from `Rte_<Swc>.h` | the same |
| Calibration values | `<Swc>_data.m` | the same (read by `tools/swc/mdata.py`, no MATLAB) |
| Build rules | `ecu/rte` (`swcComponents`, warnings of the generated code not fatal), outside the MISRA scope | `ecu/application` like the application code: `-Wall -Wextra`, warnings are errors, in the MISRA scope of `tools/misra` |
| `model.sha256` hint | yes | no (there is no model) |

**Workflow**

1. Copy `swc/template/swc.yaml` to `swc/<Swc>/swc.yaml` (or `projects/<project>/swc/<Swc>/`), set the name, runnables,
   ports, parameters and **`implementation: c`**; check with `python tools/swc/swc_yaml.py check swc/<Swc>`.
2. Create the code skeleton **once**: `python tools/swc/swc_skeleton.py create swc/<Swc>`. It writes `src/<Swc>.c` with
   `<Swc>_Init` and one function per runnable, each with commented example accesses; it never overwrites a C source, the
   file is yours from then on.
3. Write the logic. Keep the state `static`; runnables that share state belong in one task (the RTE protects port
   buffers, not the component's own variables).
4. Connect and schedule it in `rte.yaml` like any component, `path` relative to that file (RTE-22).
5. After changing runnables: `python tools/swc/swc_skeleton.py check swc/<Swc>` lists missing, `static` or duplicate
   runnable functions and orphans (warnings, exit code 0, `--strict`: 1); rtegen and the Studio show the same findings.

```yaml
# swc/LevelMon/swc.yaml
name: LevelMon
implementation: c
runnables:
  - { name: Run10ms, period_ms: 10 }
ports:
  - { name: Level_mV, direction: in, type: uint32, runnables: [Run10ms] }
  - { name: Above, direction: out, type: boolean, runnables: [Run10ms] }
parameters:
  - { name: OnLevel_mV, type: uint32, runnables: [Run10ms] }      # value in LevelMon_data.m
```

```c
/* swc/LevelMon/src/LevelMon.c */
#include "Rte_LevelMon.h"

static boolean above;

void LevelMon_Init(void) { above = FALSE; }

void LevelMon_Run10ms(void)
{
    uint32 level_mV = 0U;
    (void)Rte_Read_Level_mV_Level_mV(&level_mV);
    above = (level_mV >= Rte_Prm_CalPrm_OnLevel_mV()) ? TRUE : FALSE;
    (void)Rte_Write_Above_Above(above);
}
```

rtegen builds the same component model as from the ARXML, so the same checks apply:

- **Ports:** sender-receiver, port = interface = data element name, platform types, explicit access by exactly the
  runnables listed at the port (the task plan uses that list), init value 0.
- **Calibration parameters** are supported (scalar, row vector `dim`, fixed point `lsb`/`offset`); values come from
  `<Swc>_data.m` without MATLAB.
- **Messages:** errors carry the swc.yaml file, YAML path and line; the name in `rte.yaml` must be the name in swc.yaml;
  two runnables with the same C function are an error. **Array ports** (RTE-25) are supported, see below.
- **Not (yet) available:** records, enumerations and fixed point at ports (`types:` is refused), implicit access (RTE-09),
  mode ports (RTE-08), init values other than 0; such a component is built in Simulink.

Example and proof: test profile `tools/rtegen/test/c_swc` (C component `LevelMonSwc` next to the Simulink component
`DemoSwc`), tests `rtegen-c-swc`, `swc-skeleton`, `rtegen-reference` and the firmware build `target-c-swc`.

## Data types (RTE-04)

Besides the platform types, sender-receiver ports carry the composite and scaled types of Embedded Coder R2024a.
Test model: `tools/rtegen/test/types/TypeSwc` (`TypeSwc_create.m` builds the model, `swc_build` generates it).

| Type in the model | ARXML | In the RTE |
|---|---|---|
| vector `uint16`, dimension 4 | IMPLEMENTATION-DATA-TYPE `ARRAY` (`rt_Array_uint16_4`) | `typedef uint16 rt_Array_uint16_4[4];`, `Rte_Read(uint16* data)`, `Rte_Write(const uint16* data)`, copied with `memcpy` |
| bus (`Simulink.Bus`), also nested and with vector elements | `STRUCTURE` + APPLICATION-RECORD-DATA-TYPE | `typedef struct { ... } Name;`, `Rte_Read(Name* data)`, `Rte_Write(const Name* data)` |
| enumeration (`Simulink.IntEnumType` or `uint8` based) | `VALUE` + TEXTTABLE compu method | `typedef uint8 Name;` and one macro per literal (`#define TypeSwc_Mode_Fault ((TypeSwc_Mode)3U)`) |
| fixed point `fixdt(...)` | `VALUE` + LINEAR compu method of the application type | `typedef uint16 UFIX16_SP1_BN40;`, stored values; the scaling is checked, not converted |

Model rules, because the RTE defines these types (`Rte_Type.h`):

| Type | Rule |
|---|---|
| Bus | `DataScope = 'Imported'`, `HeaderFile = 'Rte_Type.h'`, root ports with `BusOutputAsStruct` on |
| Enumeration | `getDataScope` returns `'Imported'`, `getHeaderFile` returns `'Rte_Type.h'`, `addClassNameToEnumNames` returns `true`; a `uint8` enumeration in a MATLAB Function block needs `SimGenImportedTypeDefs` on (simulation only) |
| Fixed point in a bus | a named `Simulink.NumericType` (a bare `fixdt(...)` bus element loses its scaling in the ARXML of R2024a); at a root port a bare `fixdt` is fine |

Units of `Simulink.BusElement.Unit` and port `Unit` do not reach the ARXML of R2024a.

What rtegen checks (message with YAML path, also in `rtegen --validate` and the Studio):
- A connection between two components needs the same layout, element types, scaling and literals; records also the same
  type name.
- One type name has one definition over all components, one literal name one value.
- Records, arrays and fixed point connect only to component ports, `rte` and `rte.init`; BSW and CAN targets carry
  unscaled scalars. Enumerations are integers and may use BSW targets of their platform type (e.g. `ecu.state`).
- Init values from the ARXML (numerical, literal, array, record, constant reference); physical values become stored values,
  rounded to the nearest step and checked against the type.
- Refused: variable-size arrays, unions, anonymous nested records, compu methods other than IDENTICAL, LINEAR and
  TEXTTABLE, records as calibration parameters.

The console (`rte signals`) lists records and arrays element by element, enumerations as integer, fixed point as its
stored value.

## Named types in swc.yaml (RTE-15)

A Simulink component describes its records, enumerations and fixed-point types in the list `types:` of swc.yaml;
a port names one of them as its `type`. `swc_create` builds the model with them, so a component with composite
ports no longer has to be modelled by hand as `TypeSwc_create.m` does.

```yaml
types:
  - name: MySwc_Mode                  # enumeration
    kind: enum
    base: uint8                       # optional: uint8 (default), uint16, sint8, sint16, sint32
    literals: [Off, Run, {Fault: 3}]  # a name, or {name: value}; without a value the previous one + 1, first 0
    default: Off                      # optional, the first literal otherwise
  - name: MySwc_Speed                 # fixed point
    kind: fixed
    base: sint16                      # integer platform type of the stored value
    lsb: 0.0625                       # physical = stored * lsb + offset
    offset: 0                         # optional
    unit: rpm                         # optional
  - name: MySwc_Cmd                   # record (bus)
    kind: record
    elements:
      - { name: Mode, type: MySwc_Mode }
      - { name: Speed, type: MySwc_Speed, unit: rpm }
      - { name: Limits, type: uint8, dimension: 2 }   # array inside the record
      - { name: Enable, type: boolean }
ports:
  - { name: Cmd, direction: in, type: MySwc_Cmd, runnables: [Run10ms] }
  - { name: Speed, direction: out, type: MySwc_Speed, min: -100, max: 100, runnables: [Run10ms] }
```

| Kind | In the model (`swc_create`) | In the RTE (tools/rtegen, from the ARXML) |
|---|---|---|
| `enum` | `Simulink.defineIntEnumType` with `StorageType` = base, `DataScope` Imported, `HeaderFile` `Rte_Type.h`, class name in the literal names; model setting `SimGenImportedTypeDefs` on | `typedef uint8 MySwc_Mode;`, `#define MySwc_Mode_Fault ((MySwc_Mode)3U)` |
| `fixed` | named `Simulink.NumericType` (alias, Imported): binary point scaling for a power-of-two lsb without offset, slope and bias otherwise | `typedef sint16 MySwc_Speed;`, stored values, LINEAR compu method |
| `record` | `Simulink.Bus` (Imported), root port as nonvirtual bus (`BusOutputAsStruct`) | `typedef struct { ... } MySwc_Cmd;` |

- **Where the definitions live:** `swc_create` writes `<Swc>_types.m` next to the model (generated, do not edit); the
  model's `PreLoadFcn` and `swc_load_init` run it. After a change of `types:` write it again with
  `python tools/swc/swc_yaml.py types swc/<Swc>` (`--check` only compares). It is an input of `generated/model.sha256`.
- **Why named fixed point:** a bare `fixdt(...)` inside a bus loses its scaling in the ARXML of R2024a.
- **Checks** (YAML path and line): kind and keys per kind; literals are unique identifiers with unique values; `default`
  is a literal; fixed point needs an integer base and `lsb` > 0; record elements are platform or named types, no arrays
  of records, no recursion; an unused type is a warning. `min`/`max` are allowed for fixed point only. The typedef and
  every literal macro `<Type>_<Literal>` join the check of generated identifiers (TOOL-134).
- **Type names are global in the ECU:** prefix them with the component (`MySwc_Mode`) unless two components share a type.
- **Not (yet) covered:** C components refuse `types:`; arrays of named types at a port (use a record with an array
  element); the Studio forms and `swc_ports` know platform types only.

rtegen compares the port types of swc.yaml with the ARXML (TOOL-166): enumeration and fixed point by platform type, a
record by its name. Proof: `tools/swc/test/fixtures/TypeYamlSwc/swc.yaml`, `test_swc_yaml.py` (SwcTypesTest) and the
MATLAB test `SwcTypesTest.m`.

## Array ports (RTE-25)

A port of swc.yaml is an array of fixed size with the key `dimension` (default 1 = scalar):

```yaml
ports:
  - { name: CellVoltages, direction: out, type: uint16, runnables: [Run10ms], dimension: 96 }
  - { name: SocByOcv, direction: in, type: uint8, runnables: [Run10ms], dimension: hvscmc.number_cell_voltages }
  - { name: CellTemps, direction: in, type: sint16, runnables: [Run10ms], dimension: 16,
      dimension_source: hvscmc.number_temp_sensors }
```

| Form | Meaning |
|---|---|
| `dimension: <n>` | whole number 1..4096; the master of the generated code |
| `dimension: <expression>` | a name of the init data (or arithmetic of names and numbers, `a.n * 2`), resolved when swc.yaml is loaded (rtegen, `swc_yaml.py check`, Studio) from `<Swc>_init.m` and `init_data/*.m` next to swc.yaml - **without MATLAB** (`tools/swc/initdata.py`) |
| `dimension: <n>` + `dimension_source: <expression>` | the number with the expression it came from, as an import writes it when the expression cannot be resolved without MATLAB; checked again whenever the init data allows it |

**Decision (RTE-25): the generated code always has a number, the resolution happens in the tool.** Init data is evaluated
without MATLAB only for plain top-level assignments (`name = <number>;`, `a.b = <arithmetic>;`, numeric casts,
`Simulink.Parameter`). Anything else (`.mat` file, data dictionary, function call, assignment inside `if`/`for`) is an
error at the port with the next step: write the number MATLAB gives as `dimension` and keep the expression as
`dimension_source`. A `dimension_source` that resolves to another number is an error, one that cannot be resolved a
warning.

What follows from a dimension > 1:
- **RTE:** type `rt_Array_<type>_<n>`, `Rte_Read_<P>_<P>(<type>* data)`, `Rte_Write_<P>_<P>(const <type>* data)`, a buffer
  of the full size copied with `memcpy`; consistency between tasks as for every signal (section "Datenkonsistenz
  zwischen Tasks").
- **Connections:** both ports need the same dimension and element type; a scalar and an array do not connect.
- **CAN:** signals stay scalar; an array port at `can.*` or another BSW target is refused (one scalar port per element).
- **A2L:** one `MEASUREMENT` with `MATRIX_DIM <n>` (tools/a2lgen); the console lists the elements.
- **Simulink components:** `swc_create` sets `PortDimensions`; a different number in the ARXML is a warning (the RTE
  follows the model). **C components:** the skeleton declares an array per example access.

Proof: test profile `tools/rtegen/test/arrays`, tests `rtegen-c-swc`, `swc-yaml`, `swc-skeleton`, `a2lgen`. The Studio
import takes the dimension from the model (TOOL-143).

## Measurements (TOOL-143)

Ports are measurable over XCP anyway. An internal value becomes measurable through the list `measurements:` of
swc.yaml (decision E6 a, `doc/architecture/kalibrierung.md` section 8.1):

```yaml
measurements:
  - { name: HvsCellMod_u_CellMax, type: float32, unit: V, min: 0, max: 5, description: highest cell voltage }
  - { name: HvsCellMod_u_Cells, type: float32, dimension: hvscmc.number_cell_voltages }
```

Keys: `name` (C identifier of a global variable), `type` (platform type), `dimension` and `dimension_source` as for ports,
`unit`, `min`/`max` (TOOL-150: engineering limits, A2L `LOWER_LIMIT`/`UPPER_LIMIT`, `min <= max`, inside the type range)
and `description` (all optional).

- **Simulink component:** the model marks the signal: a named `Simulink.Signal` with storage class `ExportedGlobal` or
  `ExportToFile` (only these make Embedded Coder define a global variable). rtegen checks each entry against
  `generated/src` and only declares the variable. **With AUTOSAR the signal at a root port must keep `Auto`** (R2024a
  refuses the build otherwise); the port is measurable anyway.
- **C component:** the RTE defines the variable in `Rte.c` (initial value 0) and declares it in `Rte_<Swc>.h`.
- **RTE and A2L:** rtegen adds the rows `<Swc>.<name>` to `rte::SIGNALS` (XCP reads, never writes); tools/a2lgen makes one
  `MEASUREMENT` per entry (`MATRIX_DIM`, `PHYS_UNIT`). One name is one global symbol per ECU.
- **Limits:** `rte::SIGNALS` has no room for them, so a2lgen reads `min`/`max` (and the `unit` of a port) from the swc.yaml
  of every component of the project (`--project`); without them `MEASUREMENT` keeps the type range.

**Ports are measurable this way too:** since TOOL-150 a port may carry `unit`, `min` and `max`. The naming convention
(`doc/naming/praefix-tabelle.yaml`, `tools/studio/core/portnaming.py`) proposes them from the name's prefix at import
(`u_` → unit `V`, `pct_` → `%` and `0..100`); a prefix only proposes and warns (decision E6 a). `swc_yaml.find_prefix()` is
the one place that recognizes a prefix. Tests: `swc-yaml`, `rtegen-c-swc`, `a2lgen`, `studio-core`.

## Tasks (RTE-02)

Runnables laufen in frei konfigurierbaren Anwendungs-Tasks der ECU-Beschreibung (`rte.yaml`, Abschnitt `tasks:`):

```yaml
tasks:
  - name: motor              # Kontext TASK_MOTOR, Name in "stats"; klein, Buchstaben und Ziffern
    period_ms: 1
    priority: 45             # Ordnungsschlüssel zwischen den BSW-Tasks, siehe unten
    stack_bytes: 1536
    runnables: [MotorSwc.Run1ms]
  - name: swc
    period_ms: 10
    priority: 30
    stack_bytes: 2048
    runnables:                                     # Aufrufreihenfolge im Zyklus
      - DemoSwc.Run10ms
      - { runnable: DemoSwc.Run100ms, offset_ms: 20 }   # Aufruf bei 20, 120, 220 ms ...
can:
  task: swc                  # Pflicht bei mehreren Tasks: dort wird CAN gepackt/entpackt
```

- **Prioritäten:** `priority` ist ein Ordnungsschlüssel: BSW-Tasks aus `ecu/asyncCoreConfiguration/tasks.yaml` (background
  10, bsp 20, can 40, sysadmin 50, safety 60) und Anwendungs-Tasks werden danach sortiert und durchnummeriert
  (`tasks/async/Config.h`). Gleiche Schlüssel, Anwendungs-Tasks über `safety` (`above_application`) und mehr Tasks als
  FreeRTOS-Prioritäten sind Fehler mit YAML-Pfad.
- **Prüfungen** (YAML-Pfad, z. B. `tasks[1].runnables[2].offset_ms: 25 ms is no multiple of the period 10 ms of task app`):
  Runnable-Periode und Versatz sind Vielfache der Task-Periode, der Versatz ist kleiner als die Runnable-Periode, jedes
  periodische Runnable gehört genau einem Task, Init-Runnables keinem, die Task-Periode ist ein Vielfaches des OS-Ticks,
  der Stack mindestens `min_stack_bytes`, der Name nicht `idle`/`timer`/BSW-Task, CAN-Zyklen sind Vielfache der Periode
  von `can.task`.
- **Ohne `tasks:`** entsteht ein Task `swc` (Schlüssel 30, 2048 Byte) mit dem ggT der Runnable-Perioden.
- **Erzeugt:** `tasks/async/Config.h`, `RteTasks.h`, `rte::TASKS[]` und `rte::RUNNABLES[]`. Init-Runnables laufen beim
  Lifecycle-Init im Kontext des ersten Anwendungs-Tasks, danach verteilt `rte::init()` die Werte an alle Tasks.

## Kalibrierparameter (RTE-03)

Verstellbare Werte einer Komponente (Filterkonstanten, Schwellen, Kennlinien) sind **ParameterReceiver-Ports**: alle
Parameter einer Komponente sind Elemente des Ports `CalPrm`, das Modell liest sie mit `Rte_Prm_CalPrm_<Name>()`.

| Was | Wo (genau eine Stelle) |
|---|---|
| Name, Typ, Dimension, Festkomma-Skalierung | Modell (`.slx`); beim Anlegen aus `swc.yaml` → `parameters:` |
| **Wert** | `swc/<Swc>/<Swc>_data.m` neben dem Modell (bei Projektkomponenten `projects/<p>/swc/<Swc>/<Swc>_data.m`) |
| Ablage zur Laufzeit | RAM, von rtegen erzeugt: `Rte_Prm_<Swc>_CalPrm_<Name>_data` (nicht `const`) |

```matlab
% swc/DemoSwc/DemoSwc_data.m
FilterAlpha = 0.125;   % [-] Gewicht des NTC-Tiefpasses
```

- Die Datendatei ist gewöhnlicher MATLAB-Code im Stil der alten MBDT-`*_data.m` (Zahlen, Zeilenvektoren,
  `hex2dec`/`bin2dec`, Verweise wie `Gain = cal.gain;`). Gelesen wird sie **ohne MATLAB** von `tools/swc/mdata.py`, von
  rtegen und `swc_yaml.py`; MATLAB liest dieselbe Datei über `tools/swc/swc_load_data.m`. Ein in der `.slx` gespeicherter
  Zahlenwert oder der INIT-VALUE im ARXML zählt nie.
- Modellseitig ist ein Parameter ein `Simulink.Parameter` als **Modellargument** (`ParameterArgumentNames`), abgebildet als
  `PortParameter` (`tools/swc/swc_map_parameters.m`). Ein Parameter, den das Modell nicht benutzt, bekommt im ARXML keinen
  Typ; rtegen meldet das.
- Typen: Plattformtypen als Skalar oder Zeilenvektor (`dim`), Festkomma über `lsb`/`offset`. In der Datendatei steht der
  **physikalische** Wert; rtegen rechnet in den gespeicherten Wert um und prüft Länge und Wertebereich.
- Parameter werden in der `rte.yaml` **nicht** verbunden und brauchen keine Kopien am Task-Übergang.
- **Für XCP (TOOL-01):** `rte::PARAMETERS[]` nennt Name, Adresse, Typ, Elementanzahl, Einheit und die Umrechnung
  `physikalisch = (numerator[0] + numerator[1] · gespeichert) / denominator` (A2L `RAT_FUNC`).

## Calibration objects (RTE-26)

Stage K1 of the calibration concept ([doc/architecture/kalibrierung.md](../doc/architecture/kalibrierung.md), decision
E1 a): `swc.yaml` `parameters:` is the one definition of the calibration objects of a component, for C and Simulink
components alike. The RTE-03 keys keep their meaning; a parameter without the keys below stays exactly as before
(value or array, type range as limits, QM, generated code and A2L unchanged).

| Key | Meaning |
|---|---|
| `kind` | `value`, `array` (`dim` ≥ 2), `curve`, `map`, `axis` (shared breakpoints, `dim` ≥ 2); without `kind`: value (`dim` 1) or array |
| `axis` | curve: the name of a shared axis, or own breakpoints `{dim, type, lsb, offset, unit, min, max, description}` |
| `axes` | map: `{x: …, y: …}`, each as `axis`; in `<Swc>_data.m` and the dataset X are the rows, Y the columns (Simulink: the other way round, see below) |
| `unit`, `description` | physical unit and one line of text (A2L `PHYS_UNIT`, long identifier) |
| `min`, `max` | engineering limits, physical, inside the type range (with `lsb`/`offset`), `min` ≤ `max` |
| `safety` | `qm` (default), `asil_a`, `asil_b`; an ASIL class needs `min` and `max` (also on own axes), a shared axis needs at least the class of every curve/map using it; ASIL C/D come later |
| `value_table` | `{stored value: text}` for an integer or boolean type without `lsb` (A2L `COMPU_VTAB`) |

```yaml
parameters:
  - { name: SpeedAxis_rpm, kind: axis, type: uint16, dim: 5, unit: rpm, min: 0, max: 8000, safety: asil_b,
      runnables: [Run10ms] }
  - name: TorqueLimit_Nm            # map: X shared, Y own (TorqueLimit_Nm_Y)
    kind: map
    type: sint16
    lsb: 0.1
    unit: Nm
    min: 0
    max: 250
    safety: asil_b
    axes:
      x: SpeedAxis_rpm
      y: {dim: 3, type: sint8, unit: degC, min: -40, max: 125}
    runnables: [Run10ms]
  - { name: Mode, type: uint8, value_table: {0: Off, 1: Eco, 2: Sport}, runnables: [Run10ms] }
```

**Values.** Until the dataset of the project exists (stage K2, TOOL-145) the values stay in `<Swc>_data.m` as the defaults of
the component type. A map is a matrix with one row per X breakpoint (`[120 110 90; 150 140 120]`); own breakpoints stand
under `<Name>_X` / `<Name>_Y`. Checked without MATLAB (`swc_yaml.py`, rtegen): breakpoints strictly ascending, every
default inside `min`/`max` and the stored limits, a map of the shape its axes give, values of a value table listed in it.

**RTE.** Own breakpoints are elements `<Name>_X` / `<Name>_Y` of `CalPrm` (access `Rte_Prm_CalPrm_<Name>_X()`), stored with
the values in one record (A2L `STD_AXIS`); a shared axis is its own object (`AXIS_PTS`). A map is stored as one block per
X breakpoint with the values over Y (Y fastest; in the A2L `FNC_VALUES … COLUMN_DIR`, TOOL-196). `rte::PARAMETERS[]` gets
the limits as stored values, the rows of the axes, the kind and the safety class; texts and the layout identifier go to
`RteCalibration.json` for tools/a2lgen.

**Simulink components.** The definition in `swc.yaml` is checked against the ARXML (element type, number of values and
scaling must agree); parameters of the model that `swc.yaml` does not define stay RTE-03 parameters with a warning.

**X/Y rule for a map in Simulink (TOOL-193, TOOL-196).** In the model a map (nx X, ny Y breakpoints) is the matrix
**ny × nx**: rows run over Y (1st input of the n-D Lookup Table block), columns over X (2nd input). Because the model keeps
column-major layout, the flat order is the same as in `<Swc>_data.m` (one row per X), the dataset, the RTE and the A2L;
nothing is transposed on the way. `tools/swc/swc_params.m` refuses a matrix of the wrong form or a row-major model;
`swc_load_data.m` fills it with `reshape(flat, size(p.Value))`. A `Simulink.LookupTable` is split into `<Name>` and
`<Name>_X` (set 2) / `<Name>_Y` (set 1).

Examples: the test profile `tools/rtegen/test/calib` (C component `CalMapSwc`) and `tools/a2lgen/README.md`.

## ECU-Zustand und Sleep-Anforderung (SYS-01/SYS-02)

Die ECU-Zustandsverwaltung (`libs/bsw/ecuState`) ist über die Verbindungsziele `ecu.state`, `ecu.wakeup`,
`ecu.wakeup.<Quelle>` und `ecu.sleep_request` der ECU-Beschreibung erreichbar; Tabelle der Ziele und Beispiel: [RTE-Ports in
`ecuState.md`](../libs/bsw/ecuState/doc/ecuState.md#rte-ports).

- Mehrere `ecu.sleep_request` werden **UND-verknüpft**: geschlafen wird erst, wenn alle Ports 1 melden; eine Komponente
  kann Sleep nur zulassen, nie gegen eine andere erzwingen.
- Zeiten, Richtlinien und Aufweckquellen sind **Projektwerte** in `project.yaml` unter `modules.ecuState`
  (`projects/README.md`), nicht in der `rte.yaml`.

## Signal-Qualifier empfangener CAN-Botschaften (COM-03)

Timeout-, Prüfsummen- und Zählerprüfung laufen in der RTE, **nicht im Modell**
([`doc/architecture/signal-qualifier.md`](../doc/architecture/signal-qualifier.md)). Jede empfangene Botschaft
hat einen Qualifier, den ein Required Port (`uint8`) über `can.qlf.<Botschaft>` liest:

| Wert | Name | Bedeutung |
|---|---|---|
| 0 | `QLF_DEBOUNCE` | noch nichts Gültiges empfangen oder ein Fehler wird noch entprellt |
| 1 | `QLF_OK` | alle Prüfungen bestanden |
| 2 | `QLF_ERROR_ALIVE` | E2E-Zähler wiederholt oder falsche Folge (länger als `debounce_ms`) |
| 4 | `QLF_ERROR_CRC` | E2E-Prüfsumme oder Data ID falsch (länger als `debounce_ms`) |
| 8 | `QLF_ERROR_TIMEOUT` | keine Botschaft seit `timeout_ms` |
| 16 | `QLF_ERROR` | Bus-Fehler (Transceiver nicht bereit, Bus-off) – sofort, für alle Botschaften des Busses |
| 32 | `QLF_ERROR_RANGE` | Messwert außerhalb seines gültigen Bereichs, länger als `debounce_ms` (Bibliotheksblock `NxSwcLib/SensorQualifier`, RTE-07) |

Vorrang bei mehreren Fehlern: `QLF_ERROR` > `QLF_ERROR_TIMEOUT` > `QLF_ERROR_CRC` > `QLF_ERROR_ALIVE`. Nach
**einer** gültigen Botschaft ist der Qualifier wieder `QLF_OK`. Signalwerte übernimmt die RTE nur aus gültigen
Botschaften; im Fehlerfall bleiben sie stehen (`on_error: hold`, Vorgabe) oder fallen auf den DBC-Startwert
(`on_error: init`). Konfiguriert wird das je Botschaft in `rte.yaml` unter `can.qualifiers` (Profil P01, P02
oder P11 der AUTOSAR-E2E-Bibliothek `libs/bsw/e2e`, CRC- und Zählerlage über DBC-Signalnamen); Sendebotschaften
mit `e2e` schützt die RTE beim Senden selbst. Beispiel und Prüfungen: `projects/README.md` und die Referenz
`doc/reference/rte.md`.

Die Konstanten erzeugt rtegen als `Rte_Qlf.h` (C/C++) und `Rte_Qlf.m` (MATLAB). Im Modell wird der Qualifier wie jeder
`uint8`-Eingang verwendet (`Qlf == QLF_OK`); **als Zahl gehören die Werte nicht ins Modell**. `swc_build` und
`swc_mil_test` holen sie über `tools/swc/swc_load_qlf.m` in den Base-Workspace (im MATLAB-Desktop einmal `swc_load_qlf`).
Gesucht wird `Rte_Qlf.m` unter `NX_RTE_GENERATED`, `<repo>/build/**/generated/rte` und `%TEMP%/s32k/**/generated/rte`;
wird nichts gefunden, bricht der Lauf ab. Ein Projekt muss also **einmal konfiguriert** sein
(`cmake --preset buildtest-s32k148-min`).

**Folge für wiederverwendbare Komponenten:** Ein Qualifier-Port ist ein Required Port; ein Projekt ohne CAN kann ihn nicht
verbinden. Eine „Konstante als Quelle“ (`const.QLF_DEBOUNCE`) kennt die ECU-Beschreibung nicht.

### Datenkonsistenz zwischen Tasks

`rtegen` liest aus dem ARXML, welche Runnables welche Ports lesen oder schreiben (`DATA-RECEIVE-POINT-BY-ARGUMENTS`,
`DATA-SEND-POINTS`; fehlen alle Zugriffspunkte einer Komponente, gilt jeder Port als von jedem Runnable genutzt).
Die BSW-Übergabe eines Puffers (dio, adc, pwm, ecu.*) läuft im Task des Ports, CAN im `can.task`.

| Fall | Umsetzung | Eigenschaft |
|---|---|---|
| Puffer nur in einem Task | direkter Zugriff wie bisher | kein Lock, kein Zusatzspeicher |
| Puffer in mehreren Tasks, jeder Port in einem Task | **Kopie am Task-Übergang**: je Task eine Kopie `Rte_Cpy_<task>_…`; `readInputs(task)` übernimmt unter einem Lock alle veröffentlichten Werte, die der Task liest; `writeOutputs(task)` veröffentlicht unter einem Lock alle Werte, die er geschrieben hat | Ein Zyklus sieht einen festen Stand (zweimal lesen ergibt denselben Wert, Wertepaare eines Zyklus bleiben zusammen); Geschriebenes wird erst am Zyklusende sichtbar, Leser sehen es spätestens in ihrem nächsten Zyklus |
| Ein Port wird von Runnables in mehreren Tasks genutzt | **atomare Übergabe**: `Rte_Read`/`Rte_Write` greifen unter dem Lock direkt auf den veröffentlichten Wert zu (`RteConfig.cpp`) | jeder Zugriff für sich konsistent (auch 64 Bit), kein fester Stand über den Zyklus |

Das Lock ist `async::LockType` (FreeRTOS: Interrupts gesperrt), gehalten nur für die Kopien. Die Konsole (`rte signals`)
zeigt die veröffentlichten Werte.

## Tests

```sh
.venv/Scripts/python -m unittest tools/rtegen/test_rtegen.py   # RTE-Generator inkl. RteMultiTaskHostTest, RteTypesHostTest
.venv/Scripts/python tools/test -k rtegen-c-swc                # C-Komponenten (RTE-20)
.venv/Scripts/python tools/test -k swc-skeleton
.venv/Scripts/python tools/test -k target-c-swc
.venv/Scripts/python tools/test -k target-demo                 # erzeugte RTE des Demo-Builds
.venv/Scripts/python tools/test -k demoswc-sil                 # Software-in-the-Loop der DemoSwc
.venv/Scripts/python -m unittest tools/swc/test_swc_yaml.py    # swc.yaml, Named types (RTE-15)
```

Model-in-the-Loop (Simulink, gleiche Stimuli wie SIL, optional Vergleich mit der SIL-Spur; Pfade absolut übergeben, RTE-12):

```sh
matlab -batch "addpath('<repo>/tools/swc'); swc_mil_test('<repo>/swc/DemoSwc', '<repo>/build/host/demoswc_sil_trace.csv')"
matlab -batch "results = runtests('tools/swc/test/SwcTypesTest.m'); assertSuccess(results)"
```

Der SIL-Test von Hand (`NX_BUILD_DIR` = Build-Verzeichnis des Demo-Projekts, siehe
[`tools/test/README.md`](../tools/test/README.md), „Build directory“):

```sh
.venv/Scripts/python -m ziglang cc -std=c99 -O1 -Wall -Wextra -Werror -Ilibs/bsw/autosarBase/include \
    -I$NX_BUILD_DIR/generated/rte -Iswc/DemoSwc/generated/src \
    swc/DemoSwc/generated/src/DemoSwc.c $NX_BUILD_DIR/generated/rte/Rte.c \
    swc/DemoSwc/test/DemoSwc_sil_test.c -o build/host/demoswc_sil.exe
build/host/demoswc_sil.exe build/host/demoswc_sil_trace.csv   # optional: Spur je Runnable-Aufruf
```

## DemoSwc

Published tree: the generated code is replaced by the hand-written C substitute
[`swc/DemoSwcSubstitute`](DemoSwcSubstitute/README.md) (PLAT-119), which implements the behaviour below.

Die frühere Board-Demo als Modell:

| Runnable | Periode | Logik |
|---|---|---|
| `DemoSwc_Init` | Init | – |
| `Run10ms` | 10 ms | `Led1 = Button1`; `Led2` wechselt alle 50 Aufrufe (1 Hz blinken), **freigegeben** durch `PcCommandQlf == QLF_OK` |
| `Run100ms` | 100 ms | `MotorNtcFiltered_mV = y + (MotorNtc_mV − y) · FilterAlpha` (Tiefpass, `FilterAlpha` = 0,125 aus `DemoSwc_data.m`, RTE-03) |

Ports: `Button1` (boolean), `MotorNtc_mV` (uint32), `PcCommandQlf` (uint8, Signal-Qualifier von `PC_Command`)
als Required Ports; `Led1`, `Led2` (boolean) und `MotorNtcFiltered_mV` (uint32) als Provided Ports.

**Freigabe statt Ersatzwert (COM-13):** `DemoSwc` liest kein Signal von `PC_Command`, nur dessen Qualifier – ein
Ersatzwert hätte also nichts zu ersetzen. Stattdessen gibt der Qualifier `Led2` frei: `Led2 = Blinken AND
(PcCommandQlf == QLF_OK)`. Der Blinkzähler läuft währenddessen weiter, deshalb blinkt `Led2` nach der Heilung
phasenrichtig weiter, und die Reaktion ist innerhalb eines Aufrufs in beide Richtungen sichtbar (LED2 am Board
und `EVB_Status.Led2` auf dem Bus). `Led1` bleibt unabhängig, damit der Fehlerfall vom Tasterpfad zu
unterscheiden ist.
