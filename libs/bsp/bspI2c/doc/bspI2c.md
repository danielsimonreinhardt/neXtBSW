# I2C master contract (`bspI2c`)

Hardware-neutral interface `bsp::i2c::II2cMaster` for one I2C bus as master with 7-bit addresses. Rate, pins, time
limit and the resources of the bus are fixed by the configuration of the implementation, so device drivers written
against it stay board and family independent. The contract is header only; the implementation is the I2C driver of
a family (for s32k1xx: the FlexIO I2C master `platforms/s32k1xx/bsp/bspFlexioI2c`, reached through the family port
`platforms/s32k1xx/port/bspConfiguration`, see [Implementation on s32k1xx](#implementation-on-s32k1xx)).

It is a catalogue entry (`kind: contract`): `tools/nxconfig` and `tools/bswgen` assign no board device to it. The
values of a bus belong to the board I2C bus and the project (`i2c[]` of the board, `use.i2c` and `i2c.<bus>` of the
project), not to this contract.

**State:** verification `mock` - host tests against a fake bus and, for s32k1xx, a FlexIO register model (IO-09).
Not yet exercised on a board (see [Tests](#tests)).

## Interface

Header `i2c/II2cMaster.h`, CMake target `bspI2c` (INTERFACE).

| Method | Behaviour |
|---|---|
| `I2cResult write(uint8_t address, uint8_t const* data, size_t length, bool sendStop)` | pure virtual. START (or a repeated START after a call with `sendStop` false), address byte, `length` data bytes, STOP if `sendStop`. `length` 0 only probes the address |
| `I2cResult read(uint8_t address, uint8_t* data, size_t length, bool sendStop)` | pure virtual. Address byte, then `length` bytes (1..`maxBytesPerTransfer()`); the last byte is not acknowledged |
| `size_t maxBytesPerTransfer() const` | pure virtual. Data bytes one call accepts; longer sequences need several calls chained with repeated START |

`I2cResult`: `OK`, `NOT_INITIALIZED`, `INVALID_ARGUMENT`, `BUS_BUSY`, `ADDRESS_NACK`, `DATA_NACK`, `TIMEOUT`,
`BUS_ERROR`. Values and order are part of the contract - an implementation with an enumeration of its own converts
by value and checks the numbering with `static_assert`. The destructor is protected and non-virtual: objects are
not deleted through the interface.

Helpers in `i2c/I2cTransfer.h` (family neutral, on top of the interface):

| Function | Behaviour |
|---|---|
| `probe(bus, address)` | address byte with STOP; `OK` if a device acknowledged |
| `writeRead(bus, address, tx, txLength, rx, rxLength)` | the usual register read: write without STOP, repeated START, read with STOP. A failed write part returns its result and starts no read. `txLength` 0 reads only |
| `scan(bus, first, last)` | probes `first..last`, clamped to the device addresses 0x08..0x77 (the I2C bus specification reserves 0x00-0x07 and 0x78-0x7F). `ADDRESS_NACK` means "no device"; any other failure ends the scan and is returned with its address |
| `resultName(result)` | short text for logs and the console |

## Configuration

The contract itself has no keys ([modules reference](../../../../doc/reference/modules.md#m-bspi2c)). The bus comes
from the board and the project:

- board `i2c[]`: instance, SDA/SCL pins and, on a FlexIO, the data pins `flexio_pin`
  ([board reference](../../../../doc/reference/board.md));
- project `use.i2c` (which buses) and `i2c.<bus>` with `baudrate_hz` (upper limit of SCL, the slowest device on the
  bus decides it), `timeout_ms` (default 20) and `verify`
  ([project reference](../../../../doc/reference/project.md)). A used bus without an entry runs at 100 kHz, the
  standard mode every I2C device supports; `nxconfig check` names that default in its info lines.

`tools/nxconfig/i2c.py` checks the values with their YAML path and derives the rest (divider, resources);
`tools/bswgen` writes `bsp/i2c/i2cConfiguration.h` (`NX_I2C_BUS_COUNT`, `NX_I2C_BUSES(X)`) and `NX_USE_I2C` in
`integration/modules.cmake`.

## Implementation on s32k1xx

- Every used bus must be on a FlexIO (the family has no LPI2C driver; `nxconfig` refuses anything else, TOOL-120).
- `nxconfig` computes the SCL divider like the driver (`flexio::calculateBaud`): the fastest rate at or below
  `baudrate_hz`, at most f/6 of the FlexIO clock, at least f/512 - a slower request is an error, never rounded up.
  Above 1 MHz (Fast-mode Plus) is an error as well. The time limit must cover the longest transfer (address, 13 data
  bytes and the STOP word, 9 SCL periods each).
- Two shifters and two timers per bus, handed out in board order: bus k gets shifters 2k/2k+1 and SCL timer 2k
  (the shift timer is SCL timer + 1 and never a multiple of 4). A FlexIO of this family has 4 of each, so two buses
  fit; a third is an error at `use.i2c`. A FlexIO data pin used by two buses is an error at the board pin.
- `platforms/s32k1xx/port/bspConfiguration` (`bsp/i2c/I2cBuses.h`) has one `II2cMaster` per used bus. The FlexIO
  master is set up (`init()`) at the first transfer of its bus, in the calling task, and again after a failed
  setup. Not thread safe: all transfers of a bus must run in one task context.
- Transitional: `FlexioI2cMaster` still declares an `I2cResult` of its own with the same enumerators, so the port
  keeps the FlexIO masters (`FlexioI2cBuses.cpp`) and the contract objects (`I2cBuses.cpp`) in two translation units
  that meet through `FlexioI2cBridge.h`. Once the driver implements `II2cMaster` itself, the bridge goes away.
- Console group `i2c` (`buses`, `probe`, `scan`, `rd`, `wr`), registered in the console task when the project uses a
  bus ([console reference](../../../../doc/reference/console.md#c-i2c)). `wr` writes to a device and is a board rule:
  only to a device whose datasheet allows it.

## Limits and known gaps

- 7-bit addresses only; no 10-bit addressing, no general call, no slave operation.
- No bus sharing or locking in the contract. The s32k1xx buses are used by the console only today; a device driver
  on a bus needs the same task context or a lock in front of it.
- No bus recovery (nine SCL clocks with SDA stuck low) and no interrupt or DMA operation in the s32k1xx driver.
- The only implementation is the s32k1xx FlexIO master.

## Tests

Host-Tests, Testnamen und Verifikationsstand: [Modulkatalog](../../../../doc/reference/modules.md#m-bspi2c).
