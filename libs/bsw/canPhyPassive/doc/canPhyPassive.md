# Passive CAN transceiver (`canPhyPassive`)

Role provider `can_phy` for a CAN transceiver that software does not control: its mode pins (STB, S, EN) are
strapped to normal operation by the board and there is no status to read. The transceiver always counts as ready,
so the transmit gate of [`canSystem`](../../canSystem/doc/canSystem.md) never mutes a bus behind it.

Granularity ([`treiber-granularitaet.md`](../../../../doc/architecture/treiber-granularitaet.md)): a generic
module - no protocol and no part facts, only the role; `parts:` lists the transceivers whose mode pin a board may
hard-wire.

**State:** verification `mock` - generator and target build tests only, no bus through a passive transceiver has
been checked on a board yet (see [Tests](#tests)).

## Interface

| File | Content |
|---|---|
| `include/can/PassiveCanPhy.h` | `can::PassiveCanPhy`, derived from OpenBSW's `bios::CanPhy` (header only) |

Behaviour of the three overrides:

| Method | Result |
|---|---|
| `init(id)` | does nothing |
| `setMode(mode, id)` | records `mode` (read back through `getMode()` of the base class), returns `true` |
| `getPhyErrorStatus(id)` | always `CAN_PHY_ERROR_NONE` |

The bus id is ignored everywhere; the object has no state per bus.

## Configuration

Board (`board.yaml`, see the [board reference](../../../../doc/reference/board.md) and the
[modules reference](../../../../doc/reference/modules.md#m-canphypassive)):

- a `devices[]` entry whose `part` matches `SN65HVD1050*` or `TJA1051*`; `devices[].module: canPhyPassive`
  selects this module where several modules serve the part;
- `can[].phy` names that device as the transceiver of a bus.

Project (`project.yaml`): the module is only instantiated for buses listed in `use.can`. It has no module
parameters.

The manifest restricts `parts:` to transceivers whose mode pin a board may strap to normal operation. A board that
drives STB, S or EN from a pin needs a transceiver module with that pin instead (comment in `module.yaml`).

### Buses without a matching transceiver module

`tools/nxconfig` (`checks.py`, constant `PASSIVE`) also uses this module as the fallback provider of `can_phy`:

- a CAN bus without `phy` gives the warning "CAN bus ... describes no transceiver (phy), treated as passive
  (always ready)";
- a `phy` device whose part no module lists (and which names no `module:`) gives the warning "no module for part
  ... treated as passive transceiver (always ready)".

In both cases the bus is served by `PassiveCanPhy`.

> Example: a board with a UJA1169 system basis chip that only serves as CAN transceiver names no module for it and
> relies on this warning path, because the part is not in the `parts:` list of this manifest.
> `boards/buildtest_s32k148/board.yaml` uses `module: canPhyPassive` for two TJA1051T/3.

## Integration

- `tools/bswgen` generates `NX_CAN_PHY_COUNT_CANPHYPASSIVE`, `NX_CAN_PHY_BUSES_CANPHYPASSIVE(X)` and
  `NX_MODULE_CANPHYPASSIVE_COUNT`; the family port links the CMake target `canPhyPassive` only when a used bus
  needs it (`NX_MODULES`, `platforms/s32k1xx/port/main/CMakeLists.txt`).
- `platforms/s32k1xx/port/main/src/integration/Integration.cpp` creates one `PassiveCanPhy` object that serves
  every bus with a passive transceiver.
- The module has no bootloader hook: listing such a device in `bootloader.init` is rejected by `tools/nxconfig`
  ("device ... (canPhyPassive) has no bootloader hook").

## Limits and known gaps

- A transceiver fault, a missing supply or a wrong strap cannot be detected: the bus is always reported ready.
  Bus errors are then only visible through the CAN controller (bus state of `canSystem`).
- Sleep or standby of the transceiver is not possible; `setMode` only records the requested mode.

## Tests

Host-Tests, Testnamen und Verifikationsstand: [Modulkatalog](../../../../doc/reference/modules.md#m-canphypassive).
