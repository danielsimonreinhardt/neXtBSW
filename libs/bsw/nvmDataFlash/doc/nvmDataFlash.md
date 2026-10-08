# nvmDataFlash - data flash areas (NVM-12)

The data flash is the part of a flash module an application may erase and program at run time. On the S32K1 it is
what the FlexNVM partition leaves as data flash (`nvm.partition.data_flash_bytes`); on a derivative without a
partition it is the whole region of kind `data_flash` in mcudb. Until NVM-12 a project could reserve it but nothing
could use it. The user's purpose (2026-09-28): **calibration data changed in the device** (maps, data sets) - not
logs and not images, so an area access is enough; no file store.

## Layers

| Layer | Where | What |
|---|---|---|
| Project | `project.yaml` `nvm.data_flash.areas.<Name>` | size (whole sectors), purpose (`calibration` or `data`), optional offset |
| Check and layout | `tools/nxconfig/nvm.py` (`DataFlashPlan`, `layout_areas`) | areas fit into the data flash the partition leaves (less a calibration segment of `memory.calibration` in the same region), whole sectors, no overlap; messages with YAML path |
| Generated | `tools/bswgen` -> `nvm/nvmDataFlash.h` | `NX_DFLASH_START/SIZE/SECTOR_SIZE/PARTITION_CODE`, per area `NX_DFLASH_AREA_<NAME>_INDEX/OFFSET/SIZE/ADDRESS`, table macro `NX_DFLASH_AREAS(X)` |
| Service (family neutral) | `include/dataflash/DataFlashAreas.h` | `dataflash::AreaService`: read an area, erase an area, write into an area - one flash command per `cyclic()` step |
| Contract (family neutral) | `include/dataflash/IDataFlash.h` | `dataflash::IDataFlash`: `read`, `startErase`, `startProgram`, `poll`, `geometry`, `holdForDirectRead`/`releaseDirectRead` (NVM-25); RAII scope `dataflash::DirectRead` |
| Family | `platforms/s32k1xx/bsp/bspFlexNvm` `flexnvm/FlexNvmDataFlash.h` | FTFC Erase Flash Sector / Program Phrase on the FlexNVM data flash |

Example:

```yaml
nvm:
  partition: {eeprom_bytes: 4096, data_flash_bytes: 0x8000, partition_code: 0xB, load_on_reset: true, key_slots: 20}
  data_flash:
    areas:
      CalMain:   {size: 0x4000, purpose: calibration}
      CalBackup: {size: 0x4000, purpose: calibration}
```

## Why a contract of its own

OpenBSW's `flash::IFlashDriver` is blocking and has neither a read nor a "command running" answer. Erasing one data
flash sector takes up to 130 ms on the S32K1 (data sheet, all derivatives); during that time the task must not be
blocked and nothing may read the block. `IDataFlash` therefore launches one command and returns; `poll()` reports
its end (`BUSY`, then the result once). Every family implements it under `platforms/<family>/bsp`.

## Rules while the flash module works (s32k1xx)

- Code: the program flash stays readable during a data flash command (reference manual, "Read while write"), so the
  command is launched from the program flash with interrupts enabled; no RAM function.
- One operation per block: the FlexNVM block does a read, a program, an erase, an EEPROM store or a CSEc command at a
  time. The driver launches only with the module idle and no EEPROM store running; the EEPROM driver
  (`FlexNvmEeprom`) waits for the end of a data flash command before it stores (bounded, 260 ms). Both check and act
  with interrupts suspended for a few instructions.
- Reads: `read()` answers `BUSY` while a command or a store runs and when the module flags a read collision.
- **Block lock (NVM-25, safety review NVM12-S1 b):** one lock per image for the FlexNVM block
  (`flexnvm/FlexNvmBlockLock.h`), shared by the data flash driver and the EEPROM driver of the NvM.
  - `read()` copies in chunks of 32 bytes, each with interrupts suspended and the block checked idle inside the
    chunk; a store that another task started between two chunks makes the next chunk answer `BUSY`. While a read
    runs, no data flash command is launched (the launch answers `BUSY`). Stores never wait for a `read()`.
  - A reader that uses the data **in place** (a map evaluated where it lies) takes a hold:
    `DirectRead scope = areas.holdForDirectRead();` and `areas.areaAddress(scope, area, offset, length)` while
    `scope.held()`. A hold is granted only with the block idle; while it is held no EEPROM store starts (the store
    waits like for a command, at most 260 ms, then the write fails and the NvM reports it - it never collides) and no
    data flash command is launched. In the run-down (gate locked) no new hold is granted.
  - Assumptions of use for a hold: from a task, never from an interrupt handler; short (far below 260 ms); never
    across a blocking call; from a task whose priority is not below that of a task that stores into the emulated
    EEPROM (a higher storing task busy-waits for the hold and would starve a lower holder until its limit).
  - Not covered: CSEc commands (`bspCsec`, test builds with the user only) and PGMPART - both run with interrupts
    suspended and check CCIF, so they never overlap a chunk of `read()`, but they do not see a hold.
- Interrupt handlers read neither the data flash nor store into the emulated EEPROM.
- Cache: the S32K14x code cache holds the FlexNVM region by default; the driver calls an invalidate hook of the
  integration after every command.
- ROM check (SYS-44): every command is bracketed with the write hooks of the integration
  (`safety::romCheckFlashWrites.begin()/end()`) - per command, not per job, so a job that waits for a busy block or
  is not advanced keeps no write open (the ROM check counts an open write against its period).

## Jobs

- `erase(area)`: every sector of the area, one command per step.
- `write(area, offset, data, length)`: offset and length in whole program units (8 bytes on the S32K1). Each unit is
  read first: an erased unit is programmed and read back, a unit that already holds exactly these bytes is skipped (a
  repeated write after an interruption), any other content ends the job with `NOT_ERASED` - the flash module does not
  allow programming over data.
- `cyclic()`: `BUSY` while the job runs, its result once at its end, then `OK`. One job at a time; `data` must stay
  valid until the end.
- **Admission (NVM-26, safety review NVM23-S1 b):** the integration may hand in a `JobAdmission`; `erase()` and
  `write()` ask it first and answer `NOT_AVAILABLE` (counted in `refusedJobs()`) when it refuses. The s32k1xx port
  refuses new jobs from STANDBY on (`ecustate::isOnWayToPowerDown(ecustate::rte::publishedState())`): a job accepted
  in RUN runs on in the post-run, the run-down cuts it after the command in flight.

A power loss during an erase or program leaves the sector undefined (reference manual, CAUTION in "Flash program and
erase"). The area access does not hide that: a data set that must survive it needs two areas and a header with
check sum and sequence - the format of the calibration data set (TOOL-145, BL-31), not of this module.

## Tests

Host-Tests, Testnamen und Verifikationsstand: [Modulkatalog](../../../../doc/reference/modules.md#m-nvmdataflash).

## Integration (s32k1xx, NVM-23)

The family port (`platforms/s32k1xx/port/main`, `systems/DataFlashSystem.h`) links the module for a project with
areas: one `FlexNvmDataFlash` with the generated region and the cache hook (`cacheInvalidate()` under
`NX_FEATURE_LMEM_CACHE`), a gate for the run-down (`systems/DataFlashRunDown.h`) and one `AreaService` with the write
hooks of the ROM check, as lifecycle component `dflash` in the task of the power supply (`TASK_BSP`, steps every
10 ms, never waiting). The run-down step `ShutdownStep::NVM` locks the data flash first (no further command), runs
the NvM step and ends only when no data flash command is in flight - the power down never meets an erase. A job cut
that way ends with `NOT_AVAILABLE`. The start-up makes region 2 of the code cache (FlexNVM, FlexRAM) non-cacheable
(`PCCRMR` R2 = 00b) on every derivative with the cache. Test builds (`NX_DFLASH_COMMAND`) have the console command
`dflash show|read|erase|write|hold` (`hold <area> <ms>`: read an area in place under the block lock, NVM-25).

## Open points

- Direct reads: the lock exists (NVM-25); the calibration segment read through a pointer (production profile of
  `memory.calibration`) and XCP uploads from the data flash do not take it yet - they have to use `DirectRead` (or
  `read()`) before they may read the FlexNVM on the S32K1.
- CSEc commands (`bspCsec`) do not check the hold of the block lock yet.
- An interface for the application (RTE service or SWC port) to read and change areas; today the areas are reached
  from the owner task of the family port only.
- The command time limits are family constants from the data sheet; a derivative value in mcudb would be the right
  level.
