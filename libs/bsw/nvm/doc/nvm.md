# NvM core (`libs/bsw/nvm`)

Non-volatile blocks of an ECU on a virtual EEPROM. The core is storage neutral: it writes and reads bytes through
OpenBSW's `eeprom::IEepromDriver` (S32K1 FlexNVM EEPROM emulation, a data-flash emulation on S32K3/STM32/RM4x, an
external SPI EEPROM). Everything that protects a value - checksums, copies, write counters, repair - is a mechanism
of the BSW in this module, never of the memory driver (safety separation of `doc/architecture/README.md`).

Header only: `include/nvm/Nvm.h` (`nvm::Manager<Lock>`). Host tests: `test/test_nvm_core.py`.

## Blocks and their definition

A block is defined in `project.yaml` (NVM-18, decision of the user 2026-09-29):

```yaml
nvm:
  blocks:
    Trim:  { type: uint16 }                                  # scalar, qm
    Curve: { type: sint16, length: 8, integrity: asil_b }    # vector of 8, two copies
    Hours: { type: uint32, integrity: asil_d, address: 0x200, write: change, min_interval_ms: 60000 }
```

| Field | Meaning |
|---|---|
| `type` | platform type of the value (of one element of a vector): boolean, uint8, sint8, uint16, sint16, uint32, sint32, float32, float64 |
| `length` | 1 = scalar (default), more = vector |
| `integrity` | `qm` (default) or `asil_a` .. `asil_d`; every ASIL level uses the same two-copy mechanism |
| `address`, `write`, `min_interval_ms` | fixed place and write policy (NVM-07) |

`tools/nxconfig/nvm.py` checks the definitions and derives the size of each block in the memory; `tools/rtegen`
checks the connections `nvm.<Block>` of the ECU description against them (type and length of the writing port, with
the YAML path of the connection) and generates the block table `rte::NVM_BLOCKS`. A record port cannot be described
by type and length: its entry names only the integrity (and place/policy). A connected block without a definition
(a project from before NVM-18) is still generated with the type of its writing port and integrity `qm`; the
generator warns and proposes the definition, e.g. `nvm.blocks.Scaled: {type: uint16, length: 4, integrity: qm}`.
The blocks of the fault memory (`DemEvents`, `DemEntries`) take their layout from `diag.yaml`; their entry may name
the integrity, never a type or length.

## Formats

Every block has two places, A at `address` and B at `address + copyBytes(size, integrity)`, 4-byte aligned. All
numbers little endian (D11).

**QM** (as since NVM-02) - one current value, the other place holds the previous one:

```
[0..1] block id   [2..3] data size   [4..5] sequence   [6..6+size) data   [6+size..8+size) CRC-16/CCITT-FALSE
```

A write goes to the place that does not hold the current value; reading takes the valid place with the newer
sequence (16 bit, wrap-around). A torn write leaves the previous value.

| Situation at the start (`readAll`, QM) | Value | State | Event | Quality |
|---|---|---|---|---|
| both places valid | the newer sequence | `VALID` | - | `STORED` |
| one place valid, the other erased (written once) or from another layout | the valid place | `VALID` | - | `STORED` |
| one place valid, the other fails its CRC or cannot be read (NVM-30) | the valid place - the previous value if the defective place held the newer record | `VALID` | `PLACE_DEFECTIVE` | `STORED` |
| no valid place, at least one fails its CRC or cannot be read, or the chosen place changes between two reads | default | `CORRUPT` | `DEFAULT_LOADED` | `UNSECURED` |

**Why `PLACE_DEFECTIVE` is raised for every defective place (NVM-30):** a QM place holds either the previous record
or - after a write torn by a power loss, or when the newest record is damaged - a newer one. The sequence number of a
place that fails its CRC cannot be trusted (a single bit turns 19 into 17), so the manager cannot tell the two apart
and reports both: a fallback to the previous value is never silent. A damaged *previous* record (the board finding
of 2026-10-02, `nvm inject EcuStatistics a` with the newest record in B) loses nothing but is reported as well. The
next write of the block goes to the defective place and replaces it; until then every start reports it again. The
quality stays `STORED`: the value is a stored one. A function that must not run on a possibly previous value uses an
ASIL block (two copies of the current value), not a QM block.

**ASIL** (NVM-18, decision `nvm-block-integritaet: zwei_kopien`) - two copies of the current value:

```
[0..1] block id   [2..3] data size   [4..7] write counter   [8..8+size) data   [8+size..12+size) CRC-32 (IEEE)
```

| Situation at the start (`readAll`) | Value | State | Event | Quality |
|---|---|---|---|---|
| both copies valid, same counter, same data | stored | `VALID` | - | `STORED` |
| both valid, different counters (a write interrupted between the copies) | the higher counter; the other copy is brought up to date | `VALID` | - | `STORED` |
| one copy defective (CRC, other id/size, erased, unreadable, or other data at the same counter) | the good copy; the defective one is rewritten from it | `REPAIRED` | `COPY_REPAIRED` | `STORED` |
| same, but the rewrite fails | the good copy | `DEGRADED` | `COPY_REPAIR_FAILED` | `STORED` |
| both defective | default | `CORRUPT` | `DEFAULT_LOADED` | `UNSECURED` |
| both erased (never written) | default | `EMPTY` | - | `DEFAULT` |
| both from another layout (other id: renamed, retyped, resized, other integrity) | default | `FOREIGN` | - | `UNSECURED` |
| no memory, or the block beyond it | default | `NO_MEMORY` / `OUTSIDE` | - | `NO_MEMORY` |

A write puts the same record (counter + 1) into both copies one after the other, first into the copy that does not
hold the winning value, and reads each back before it goes on. A power loss at any byte therefore leaves at least one
valid copy: the one that is not being written (host test: power loss at every byte of both records). A QM block
whose places both fail their CRC also reports `DEFAULT_LOADED`; a QM block with one defective place reports
`PLACE_DEFECTIVE` (see above).

The record id is derived by the generator from block name and type; an ASIL block gets another id than the same
block as QM, so a changed integrity starts from the default instead of reading the other format.

## Events for the fault memory

`IEventSink::onBlockEvent(index, block, event)` is called from `readAll()`, outside the lock of the RTE. The events
(`COPY_REPAIRED`, `COPY_REPAIR_FAILED`, `DEFAULT_LOADED`, and for QM blocks `PLACE_DEFECTIVE`) reach the fault memory through the integration (NVM-19,
section "Integration"); the DTC numbers belong to the project's diag.yaml (TOOL-178/NVM-16), not to this module.

## Quality for the application

The manager writes `nvm::Quality` of every block into `BlockConfig::quality` (the RTE's `rte::nvmQuality[]`, under
the lock). A required port connected to `nvm.qlf.<Block>` reads it as a qualifier (`QLF_*` of `Rte_Qlf.h`), set by
`rte::nvmRestore()` before the init runnables:

| `nvm::Quality` | Qualifier |
|---|---|
| `NOT_READ` | `QLF_NOT_AVAILABLE` (RTE-28; the buffer starts with it) |
| `STORED` | `QLF_OK` |
| `DEFAULT` (never written) | `QLF_NOT_AVAILABLE` (RTE-28) |
| `UNSECURED` (value lost, default) | `QLF_ERROR_CRC` |
| `NO_MEMORY` | `QLF_ERROR` |

The qualifier describes where the value came from at the start; it does not change while the ECU runs. The numbers
of `nvm::Quality` are a contract with `tools/rtegen` (`NVM_QUALITY`) and stay as they are.

## Fault injection (test builds only)

With `NX_NVM_FAULT_INJECTION` defined, `Manager::injectCopyFault(block, copy)` flips one bit of the checksum of place
A (0) or B (1) in the memory, so the next start finds that place defective. Without the macro the function does not
exist (host test `NvmCoreBuildTest`); a product build never contains it.

## Integration (NVM-19)

The application uses this library (the former NVM-02 copy `ecu/application/src/systems/nvm/NvmManager.h` and its host test
are gone; the QM tests live on in `test/host/NvmHostTest.cpp`):

- `CMakeLists.txt` lists `libs/bsw/nvm` among the modules, `ecu/application/CMakeLists.txt` links `nvm` and defines
  `NX_NVM_FAULT_INJECTION` for every build except the product build (`NX_PRODUCT_BUILD`, SYS-74).
- `ecu/application/src/systems/nvm/NvmRteBlocks.h` builds the block table of the core from `rte::NVM_BLOCKS` with
  `integrity` (`asil_a`..`asil_d` -> `Integrity::ASIL`) and `quality` (buffer of `nvm.qlf.<Block>`); the scratch is
  `rte::NVM_SLOT_MAX` (ASIL copies included).
- `RteSystem` gives the manager an `IEventSink` (`nvm::EventLog`, `ecu/application/src/systems/nvm/NvmEvents.h`)
  before `readAll()`. The events arrive before the fault memory is up (it loads itself from two NvM blocks), so they
  are recorded per block and handed to the fault memory right after its start (`nvm::reportToFaultMemory`). One
  table, `nvm::DEM_EVENTS`, names the BSW event of each kind; the DTC of a block carries `<prefix>.<Block>` as its
  diag.yaml `dtcs[].event.bsw`:

  | Event | BSW event of block `<Block>` | DTC (TOOL-178, `tools/nxconfig/bswdtc.py`) |
  |---|---|---|
  | `DEFAULT_LOADED` | `nvm.default_loaded.<Block>` | `bsw_dtc_range.base` + `0x06nn` |
  | `COPY_REPAIR_FAILED` | `nvm.copy_repair_failed.<Block>` | base + `0x07nn` |
  | `COPY_REPAIRED` | `nvm.copy_repaired.<Block>` | base + `0x08nn` |
  | `PLACE_DEFECTIVE` (QM, NVM-30) | `nvm.place_defective.<Block>` | base + `0x09nn` (`bswdtc.nvm_dtc_entries(..., place_defective=True)` for a QM block) |

  `nn` is the index of the block. Every block that was read gets one result per start for each of its DTCs: `FAILED`
  when it raised the event, `PASSED` when not; a block without a memory or beyond it gets none (not checked). The
  numbers stand in diag.yaml only, never in the code; such a DTC should confirm on one report, like the start-up
  check of the calibration segment.
- Console `nvm status`: the memory, the events of the start (count and first block per kind) and per block its
  integrity, state, quality, sequence/counter, writes, errors, repairs. `seq` and `slot` are those of the current
  stored value *now*: after a write since the start (`writes` > 0) they are no longer the place that was loaded. In every build except the product build,
  `nvm inject <block> <a|b>` damages copy A or B of a block (NVM-07, NVM-30).
