# mcudb – derivative database

`mcudb/` holds the manufacturer facts of every supported MCU derivative: memory map, peripheral instances, clock
limits, interrupt priority bits, MPU size, packages and pin multiplexing. Generators and checks (`tools/nxconfig`,
`tools/bswgen`) read it; nothing in it depends on a board or a project.

## Layout

| Path | Contents |
|---|---|
| `<family>/<DERIVATIVE>.yaml` | one derivative (schema: `Derivative` in `tools/nxconfig/model.py`) |
| `<family>/<DERIVATIVE>_<PACKAGE>.pinmux.yaml` | pin multiplexing per package, **generated** from vendor pin data, never typed |
| `<family>/pinout/<PART>.yaml` | package pinout of the pin table `<PART>.pinmux.yaml`: every pin number or ball with its pad or supply/special terminal (TOOL-162, Studio package view), **generated**; format and generators: `tools/pinout_common.py`, `tools/pinout_stm32_open_pin_data.py`, `tools/pinout_nxp_rm.py` (S32K1xx reference manual on workstation level), `rm4x/tools/pinout_ti_docs.py` |
| `<family>/sources/<origin>/` | vendor files with an open licence, copied unchanged, with `README.md` (origin, commit) and `LICENSE` |
| `tools/` | pin table generators, device header tools and the tests of the database (`test_mcudb.py`) |

Families today: `s32k1xx`, `s32k3xx`, `stm32`.

## The rule on sources

Every value needs **public evidence** (PLAT-46): a data sheet or reference manual as published by the manufacturer,
or a file with an open licence, cited with document, revision and section, table or page in the entry's `source:`.
Values from documents the manufacturer has not published, or from vendor files whose licence forbids redistribution, do not go in. A value without public
evidence is marked `verify: true` with a comment saying why. The test `test_mcudb.py` rejects citations of the
S32 Design Studio device headers and checks that every source folder names its origin and licence.

Details, the allowed sources and a walk-through for a new derivative:
[`doc/guide/new-derivative.md`](../doc/guide/new-derivative.md).

## Tests

```sh
.venv/Scripts/python tools/test -k mcudb
```
