# a2lgen - A2L for XCP on CAN (TOOL-01)

`a2lgen.py` writes the A2L of an application built with the neXtBSW RTE. The build runs it after linking
(`ecu/xcp/CMakeLists.txt`, target `a2l`); the file lands next to the image as `app.ecu.a2l`.

| Input | What it provides |
|---|---|
| `app.ecu.elf` | addresses, and the tables `rte::SIGNALS` / `rte::PARAMETERS` themselves (name, element type, count, unit, conversion), read from the initialised data of the image |
| `generated/rte/RteConfig.h` | numbering of `rte::SignalType` |
| `xcp/xcpCanConfiguration.h` | XCP bus and CAN identifiers - the header the target is compiled with |
| `generated/board/bsp/can/canBitTimingConfiguration.h` | bit rate of the XCP bus |
| `project.yaml` | checks only: XCP identifiers against `diagnostics` and the DBC of the same bus |

Output: `MEASUREMENT` per RTE signal (read only; an array port as one `MEASUREMENT` with `MATRIX_DIM <n>` at its
first element - the rows `Port[0]..Port[n-1]` of `rte::SIGNALS` at consecutive addresses, RTE-25), `CHARACTERISTIC` `VALUE`/`VAL_BLK` per calibration parameter
with `COMPU_METHOD RAT_FUNC` from `physical = (n0 + n1 * stored) / d`, and `IF_DATA XCP` (`PROTOCOL_LAYER`,
`XCP_ON_CAN`). The AML that describes `IF_DATA XCP` belongs to the calibration tool and is not part of the repository;
`-DNX_XCP_A2L_AML=<file>` adds an `/include` line for it.

### Calibration objects (RTE-26)

The definitions of `swc.yaml` (swc/README.md, "Calibration objects") reach the A2L through the image: each row of
`rte::PARAMETERS` carries the stored limits, the rows of its axes, its kind and its safety class; the texts
(description, value table) come from `RteCalibration.json`, which tools/rtegen writes next to `RteConfig.h`. a2lgen
refuses a description file that names other objects or kinds than the image.

| Object (swc.yaml) | A2L |
|---|---|
| value / array | `CHARACTERISTIC VALUE` / `VAL_BLK` with `NUMBER` (unchanged for an RTE-03 parameter) |
| curve, map | `CHARACTERISTIC CURVE` / `MAP` with one `AXIS_DESCR` per axis (X first). A map is stored as one block per X breakpoint with the values over Y (Y running fastest, the order of `<Swc>_data.m` and the RTE); in the A2L view X runs across the columns, so that is `FNC_VALUES … COLUMN_DIR` in a record layout of its own (`RL_MAP_<type>`, `RL_MAP_<parts>`). Up to TOOL-196 a2lgen wrote `ROW_DIR` there, which a tool reads with X running fastest, i.e. transposed; values, arrays and curves keep `ROW_DIR` (one dimension, same order) |
| shared axis | `AXIS_PTS` (record layout `RL_AXIS_<type>`), used through `COM_AXIS` + `AXIS_PTS_REF` |
| own breakpoints | `STD_AXIS`: part of the record of the curve/map (`RL_STD_…` for a curve, `RL_MAP_…` for a map, with `FIX_NO_AXIS_PTS_X/Y`, `AXIS_PTS_X/Y`, `FNC_VALUES`); a2lgen checks that they lie in front of the values as the record layout says |
| `min`/`max` | lower/upper limit (from the stored limits in the image), the type range as `EXTENDED_LIMITS` |
| `value_table` | `COMPU_METHOD … TAB_VERB` with `COMPU_TAB_REF` and a `COMPU_VTAB` |
| `description`, `unit` | long identifier, `PHYS_UNIT` |
| component | one `FUNCTION <Swc>` with `DEF_CHARACTERISTIC` of its calibration objects |

**EPK.** `ecu/xcp` places the identity of the build in the image (`xcp::EPK`, `src/systems/XcpEpk.cpp`); the text
comes from `epk.py`, which the build runs every time (target `xcpEpk`, the header changes only when the text does):
`<project> neXtBSW <version> <git describe of the project tree>`, with a hash of the uncommitted changes when the
tree is dirty. a2lgen writes `MOD_PAR` with `EPK` and `ADDR_EPK` from the image, so the A2L always matches the ELF,
and a calibration tool notices an A2L of another build. The application logs it at start (`XCP EPK …`). The text is
readable over XCP at `ADDR_EPK` and nowhere writable (TOOL-151): `SHORT_UPLOAD` returns it, `DOWNLOAD` there gets
`ERR_WRITE_PROTECTED`; `xcp_master.py … epk [--try-write]` does the same comparison a calibration tool does.

**No page switching.** `PROTOCOL_LAYER` announces only commands the slave of `ecu/xcp` implements, and not
`SET_CAL_PAGE`/`GET_CAL_PAGE` (TOOL-151): the ECU uses one page (the RAM values), the slave accepts page 0 only, and
the A2L has no `SEGMENT`/`PAGE` description. A calibration tool therefore works on the one page instead of expecting
a switchable reference/working page. With a calibration segment the flash copy is readable at its flash address.

**Formal check.** `a2lcheck.py` checks an A2L of this subset without a tool - tokens, `/begin`/`/end`, the fixed
parameters and keywords of each block, limits against `EXTENDED_LIMITS`, the axes of `CURVE`/`MAP` against their
record layouts, and every reference (conversion, record layout, value table, axis points, `FUNCTION` entries):

```sh
python tools/a2lgen/a2lcheck.py <build>/ecu/application/RelWithDebInfo/app.ecu.a2l
```

`xcp_master.py` reads the new objects as well: `read`/`write` of a curve or map address its values, own breakpoints
are `<Name>_X` / `<Name>_Y`, a shared axis is its `AXIS_PTS` name.

The table layout is fixed for 32 bit targets; `ecu/xcp/src/systems/XcpSystem.cpp` checks it with `static_assert`,
`a2lgen.py` (`SIGNAL_LAYOUT`, `PARAMETER_LAYOUT`) reads it. Little and big endian images are both read.

**Fixed point signals (RTE-29).** A port whose type has a linear conversion (Simulink `fixdt`, swc.yaml `types:` with
`kind: fixed`) gets a row in `rte::SIGNAL_SCALINGS` next to `rte::SIGNALS` (`SCALING_LAYOUT`: signal row, decimals,
unit, `physical = (n0 + n1 * stored) / d`). a2lgen gives its `MEASUREMENT` the `COMPU_METHOD` `RAT_FUNC` of that
conversion (the same one a parameter with the same function uses), the unit as `PHYS_UNIT` and the converted type
range as limits; `xcp_master.py read`/`watch` then print physical values. The table and the define
`NX_RTE_HAS_SIGNAL_SCALING` in `RteConfig.h` exist only when the ECU has such signals: header and image must agree,
otherwise a2lgen stops (an image without the table would show stored values as physical ones).

## PC side: `xcp_master.py`

A small master on python-can for the A2L above - enough for board checks, not a calibration tool:

```sh
python tools/a2lgen/xcp_master.py --a2l <build>/ecu/application/RelWithDebInfo/app.ecu.a2l list
python tools/a2lgen/xcp_master.py --a2l app.ecu.a2l --interface vector --channel 0 read DemoSwc.CalPrm.FilterAlpha
python tools/a2lgen/xcp_master.py --a2l app.ecu.a2l write DemoSwc.CalPrm.FilterAlpha 1.0
python tools/a2lgen/xcp_master.py --a2l app.ecu.a2l epk --try-write   # EPK of the ECU = A2L, write refused
python tools/a2lgen/xcp_master.py --a2l app.ecu.a2l watch DemoSwc.MotorNtc_mV.MotorNtc_mV \
    DemoSwc.MotorNtcFiltered_mV.MotorNtcFiltered_mV --period 0.1
```

Identifiers and bit rate come from the A2L. Measurement is polling (`SHORT_UPLOAD`); a write is one `DOWNLOAD` block,
which the slave applies as a whole.

## Tests

- `python tools/a2lgen/test_a2lgen.py` - images cross-compiled with zig (arm and armeb) from the RTE of the RTE-03
  test profile; addresses, types, conversions, byte order and the error messages. RTE-26: the calibration profile
  `tools/rtegen/test/calib` with the EPK of `ecu/xcp` - CURVE/MAP/AXIS_PTS, record layouts, limits, value tables,
  FUNCTION, EPK; every A2L through `a2lcheck.py`, the values read back from the image through `xcp_master.py`, and
  broken copies of the A2L that the check must refuse.
- `python tools/a2lgen/test_map_index_mode.py` - TOOL-196: the maps of the Simulink component
  `tools/studio/tests/profiles/MapImpSwc` (zig image, a2lgen) read by a small reader of its own under both index
  modes: only `COLUMN_DIR` gives the values of `MapImpSwc_data.m` and the MIL result of TOOL-193.
- `python ecu/xcp/test/test_xcp_host.py` - the slave engine on the host, driven by `xcp_master.py` over a simulated
  CAN channel.
