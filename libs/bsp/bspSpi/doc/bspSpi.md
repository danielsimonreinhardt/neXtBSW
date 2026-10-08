# SPI device contract (`bspSpi`)

Hardware-neutral interface `bsp::spi::ISpiDevice` for one SPI slave behind a master. Bus instance, chip select,
mode and frame size are fixed by the configuration of the implementation, so device drivers written against it stay
board and family independent. The contract is a header only; the implementation is the SPI driver of a family
(for s32k1xx: `platforms/s32k1xx/bsp/bspLpspi`, class `bsp::spi::Lpspi`).

It is a catalogue entry only (`kind: contract`): `tools/nxconfig` and `tools/bswgen` assign no board device, take no
project values and generate nothing for it.

**State:** verification `hardware` for 16-bit and 40-bit frames through the s32k1xx LPSPI implementation; other
frame sizes only in host tests (see [Tests](#tests)).

## Interface

Header `spi/ISpiDevice.h`, CMake target `bspSpi` (INTERFACE).

| Method | Behaviour |
|---|---|
| `bool transfer(uint32_t tx, uint32_t& rx)` | pure virtual. Full-duplex transfer of one frame, chip select asserted for its duration. Only the configured number of low-order bits of `tx` is sent and returned in `rx`. `false` on timeout or bus error; `rx` is then undefined |
| `bool transferLong(uint64_t tx, uint64_t& rx)` | one frame of up to 64 bits, chip select held for the whole frame, most significant configured bit first. Not pure virtual: the default returns `false`, so implementations limited to 32 bits and test fakes stay unchanged. `false` also if the implementation does not support the configured frame size |

The destructor is protected and non-virtual: objects are not deleted through the interface.

## Configuration

The contract itself has no keys ([modules reference](../../../../doc/reference/modules.md#m-bspspi)). A device driver
states its need with `requires: { spi_device: 1 }` in its manifest (today `sbcUja113x` and `gdTmc6200`; the parked
`bccMc33771` manifest as well). `tools/nxconfig` then checks that such a device is reached over exactly the required
number of used SPI buses (`checks.py`, ARCH-44). The bus, its pins and the chip select come from `spi[]` of the board
and `use.spi` of the project ([board reference](../../../../doc/reference/board.md#f-spi-list),
[project reference](../../../../doc/reference/project.md#f-use)).

## Behaviour

What the contract promises is stated above; everything else - clock, mode, frame size, timeout, context - is
defined by the implementation.

> Example (s32k1xx implementation `Lpspi`, not part of the contract): `transfer` handles frames of up to 32 bits and
> returns `false` for a longer configured frame; `transferLong` handles 8..64 bits except 33, sending frames above
> 32 bits as two transmit words in one chip select period. The class is not thread safe: all transfers must run in
> one task context.

## Limits and known gaps

- One slave per `ISpiDevice` object; there is no bus sharing or locking in the contract. Whether several objects on
  one bus may be used from different tasks is up to the implementation (the s32k1xx one requires one task
  context).
- Only master operation; `doc/architecture/README.md` lists SPI as "present, master only LPSPI".
- The only implementation today is the s32k1xx LPSPI driver.
- A proposal to offer this contract to Eclipse OpenBSW is prepared in
  [`doc/openbsw-beitraege/12-spi-adc-contracts`](../../../../doc/openbsw-beitraege/12-spi-adc-contracts/issue.md).

## Tests

Host-Tests, Testnamen und Verifikationsstand: [Modulkatalog](../../../../doc/reference/modules.md#m-bspspi).
