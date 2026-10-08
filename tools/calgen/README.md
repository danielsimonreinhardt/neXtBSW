# calgen – calibration datasets (stage K2)

`calgen` manages the **values** of the calibration parameters of a project: the dataset of stage K2 of the
[calibration concept](../../doc/architecture/kalibrierung.md) (decisions E2 a and E3 a, TOOL-145). The **definition**
of each calibration object (kind, type, axes, unit, limits, safety class) stays in the component's `swc.yaml`
(stage K1, RTE-26), and the defaults of a component type stay in `<Swc>_data.m`. A dataset belongs to the project
and wins over the defaults in the build.

No MATLAB and no Qt: `calgen` loads the components the way `tools/rtegen` does and judges every value with the same
functions as the build.

| File | Content |
|---|---|
| `caldata.py` | the format `nextbsw-cal 1`: read (findings with file and line), write, patch keeping comments, resolve a variant, compare |
| `calgen.py` | command line and the functions behind it (migrate, check, rebind, resolve, diff, export, import, values) |
| `exchange.py` | DCM and CDFX, reader and writer |
| `nx_cal_load.m` | MATLAB: loads a dataset into the workspace or into the model arguments of a model |
| `test_calgen.py` | host tests (`tools/test -k calgen`) |

## Quick start

```sh
PY=.venv/Scripts/python
$PY tools/calgen/calgen.py migrate projects/<p>/project.yaml --author "A. Person"   # dataset from the defaults
# edit projects/<p>/calibration/base/<Swc>.cal.yaml, or import a DCM/CDFX from a calibration tool
$PY tools/calgen/calgen.py check projects/<p>/project.yaml --all                   # what the build will say
cmake --preset <preset>                                                           # re-configure once: rtegen
cmake --build --preset <preset>                                                   # now takes the dataset
```

Creating the folder `calibration/` is not seen by an existing build directory; configure once more (later changes of
the dataset files are configure inputs, see below).

## Layout and format

```
projects/<p>/calibration/
  dataset.yaml                        format, layout identifier, optional build_variant
  base/<Component>.cal.yaml           every value of the component
  variants/<v>/<Component>.cal.yaml   only the values that differ from base (delta)
```

`dataset.yaml`:

```yaml
format: nextbsw-cal 1
layout: "a5f879e0"          # layout identifier of the software (RteCalibration.json of the build)
build_variant: sport        # optional: the variant the build takes; without it base
```

A component file (the example of the test profile, shortened):

```yaml
format: nextbsw-cal 1
component: CalMapSwc
values:
  MaxTorque_Nm:
    value: 250                        # physical, as in <Swc>_data.m
    status: released                  # draft | checked | released
    author: "neXtBSW example"
    reason: "default of the component type (CalMapSwc_data.m)"
  TorqueLimit_Nm:                     # map: one row per X breakpoint, one column per Y breakpoint
    value:
      - [120, 110, 90]     # SpeedAxis_rpm = 0 rpm
      - [150, 140, 120]    # SpeedAxis_rpm = 1000 rpm
    status: released
  Mode:
    value: "Eco"                      # a value table text or its number
    status: checked
```

Rules:

- Names are the element names of `swc.yaml` (as in `<Swc>_data.m`). Own breakpoints of a curve or map are entries of
  their own (`<Name>_X`, `<Name>_Y`), shared axes too.
- A scalar is a number, an array, curve or axis a list, a map a list of rows. `true`/`false` are 1/0.
- **base must be complete.** A missing value is an error; a default is never taken silently (`migrate` adds missing
  values as an explicit step).
- A variant entry replaces the whole base entry (value, status, author, reason). A variant cannot add a value base
  does not have. `check` warns about a delta entry equal to base.
- Status: `draft` → `checked` → `released`. A changed value goes back to `draft`: a DCM import does that, a CDFX
  import takes the state the file gives; by hand it is the author's duty, and the Git diff shows it.
- Files written by `calgen` are regenerated in a fixed order (parameter order of `swc.yaml`); the row comments of maps
  are written by the tool, other comments are not kept. `caldata.patch_component` (TOOL-198, used by the Studio when
  it takes values calibrated live over XCP into the dataset) changes only the lines of the changed entries and keeps
  every other line, comments included; a file not in the writer's plain block form is rendered anew.

The **layout identifier** is a hash over name, kind, type, dimensions, axes, scaling and order of all calibration
objects of the software (RTE-26). A dataset fits every build with the same layout; a bug fix does not invalidate it.
When the layout changes (a parameter added, a type changed), the build refuses the dataset until `calgen check` passes
and `calgen rebind` records the new identifier.

## Commands

`<target>` is a `project.yaml` (dataset `calibration/` next to it, ECU description from `project.rte`) or an ECU
description `rte.yaml` (dataset next to it, for test profiles). `--dataset DIR` names another folder.

| Command | Does |
|---|---|
| `migrate <target> [--author A] [--status S] [--reason R]` | creates the dataset from the `<Swc>_data.m` defaults (structure from `swc.yaml`), bound to the current layout; on an existing dataset it only adds missing values |
| `check <target> [--variant V \| --all] [--release] [--build DIR]` | the checks of the build (complete, known names, shape, value table, type and grid, engineering limits, strictly ascending axes), layout identifier against the software and, with `--build`, against a build's `RteCalibration.json`; `--release`: every safety-relevant value (`safety` not `qm`) must be `released`, others are warnings. Exit code 1 on an error |
| `rebind <target>` | writes the software's layout identifier into `dataset.yaml`, only if base and every variant pass the value checks |
| `resolve <target> --variant V [-o DIR]` | base plus delta as one complete dataset (printed, or written to `DIR`) |
| `diff <A>[@V] <B>[@V]` | differences of values (cell by cell), status, author, reason; `A`/`B` are dataset folders, `project.yaml` or `rte.yaml` |
| `export <target> [--variant V] --format dcm\|cdfx -o FILE [--date D]` | exchange file for a calibration tool (only a dataset that passes the checks) |
| `import <target> FILE [--variant V] [--author A] [--reason R] [--dry-run]` | takes the values of a `.dcm`/`.cdfx`; only changed values are written; into a variant only what differs from base (a value back at base leaves the delta); unknown objects, wrong kinds or sizes and values the build would refuse abort the import without writing |
| `values <target> [--variant V] [--json] [-o FILE]` | the resolved, checked values: physical, shape and stored values per parameter, plus `layout` and `values_id` |

## Build connection

`tools/rtegen` looks for `calibration/` next to the `project.yaml` (only for the project's own ECU description). With
the folder:

1. the values of base, or of `build_variant` (base + delta), replace the `<Swc>_data.m` defaults in the generated RTE
   (`Rte.c`, record initialisers);
2. every value passes the same checks as above **before anything is written**, including the layout identifier; the
   Studio check (`rtegen --validate`, TOOL-113) reports the same error;
3. `RteCalibration.json` gets `"dataset": {"name": ..., "values": ...}` – `values` is a hash over the stored values of
   all parameters, the same as `calgen values` reports;
4. `rte_components.cmake` lists the dataset files as configure inputs, so an edit runs rtegen again.

Without the folder every generated file stays byte for byte as before (`tools/test -k rtegen-reference`).
`<Swc>_data.m` stays required: it is the default of the component type, the MIL value and the source of `migrate`.

## Release

A release of a dataset is an **annotated Git tag** on a commit where `calgen check --release` passes for what is
released:

```
cal/<project>/<dataset>/v<N>           dataset: base or the variant name, N counting up per dataset
git tag -a cal/evalboard_motor/sport/v3 -m "sport v3: boost curve after bench run 2026-10-02"
```

The tag message says what changed; the tagged commit holds the dataset files and `dataset.yaml` with the layout they
are bound to. `calgen` does not create tags. The generated segment HEX and the report of safety-relevant parameters
belong to stage K3 (SYS-89) and K6.

## Exchange formats

Both are edges, not the store. The writers put out only what neXtBSW has (value, array, curve, map, shared axis,
value table); names are the A2L names `<Component>.<Port>.<Element>`, own breakpoints are part of their curve or map.
The format descriptions in our own words are at the top of `exchange.py`.

- **DCM 2.0** (text): `FESTWERT`, `FESTWERTEBLOCK`, `KENNLINIE`/`GRUPPENKENNLINIE`, `KENNFELD`/`GRUPPENKENNFELD`
  (per Y breakpoint the values over X), `STUETZSTELLENVERTEILUNG`, value table texts as `TEXT`. No status: the writer
  puts status, author and reason into a comment, the reader sets `draft` for a changed value.
- **CDFX** (ASAM CDF 2.0, XML): one `SW-INSTANCE` per object (`VALUE`, `VAL_BLK`, `CURVE`, `MAP`, `COM_AXIS`); a map
  has one `VG` per X breakpoint; status as `STATE` (draft → `prelimcalibrated`, checked → `checked`, released →
  `completed`), author as `CSUS`, reason as `REMARK` – they survive a round trip.

Not checked against a third-party calibration tool yet (no CANape/INCA here); the round trips are tested.

## MATLAB

```matlab
addpath('tools/calgen');
nx_cal_load('projects/p/project.yaml');                       % base values as variables of the base workspace
nx_cal_load('projects/p/project.yaml', 'Variant', 'sport');   % a variant
nx_cal_load('projects/p/project.yaml', 'Model', 'CalSwc');    % model arguments of a loaded model (like swc_load_data)
```

Variables are named and shaped as in `<Swc>_data.m` (a map as matrix, one row per X breakpoint). It calls
`calgen.py values --json` through the Python of the repository. The base-workspace path was run in MATLAB R2024a; the
`Model` option is not verified yet (stage K5, TOOL-146).

## Interfaces for the next stages

- **K3 (SYS-89, calibration segment):** `calgen.values_of(software, name)` / `calgen values --json` give per parameter
  the A2L name, physical and **stored** values in memory order, plus `layout` and `values_id`; `RteCalibration.json`
  of a build names the dataset and the same `values_id`. A segment HEX needs only these plus the addresses of the ELF.
  `rtegen.use_calibration_dataset()` is the one place where the build takes the values.
- **K9 (TOOL-148, Studio page):** everything is Qt-free Python – `caldata.load/resolve/diff/write_part`,
  `calgen.open_target/check/migrate/import_file/export`, findings with file and line (`caldata.Finding`).
