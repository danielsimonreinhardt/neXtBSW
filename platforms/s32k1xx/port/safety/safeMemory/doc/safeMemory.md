# Memory safety of the s32k1xx family (`safeMemory`)

Port module of the s32k1xx family, a modified copy of the OpenBSW module of the same name (tracked in
`tools/openbswsync/copies.yaml`): the MPU gate of the safety RAM (SM-10, SM-11), the ECC supervision of RAM and flash
(SM-15, SM-16) with the start-up latent-fault test of the RAM ECC (SM-55, since SYS-73) and the ROM check (SM-14). The safety concept describes the mechanisms
([`doc/safety/concept.md`](../../../../../../doc/safety/concept.md) section 6.2), the failure analysis rates them
([`doc/safety/fmea.md`](../../../../../../doc/safety/fmea.md) SM-10 … SM-16).

It has no board device. Its one project decision is the re-check period of the ROM check, a module parameter of the
project (`modules.safeMemory`, `project_parameters: true`, since SYS-44).

**State:** verification `hardware` for the first ROM-check pass (board 2026-09-21: a valid image passes, an image with
one changed character is held in the safe state); the re-check period, the known-answer test of the CRC unit and the
suspension during flash writes are host tests only (board checks B1 and B9 of `fmea.md` section 8).

## ROM check (SM-14)

| Part | Where |
|---|---|
| Checksum list (five areas behind the image) | linker template `platforms/s32k1xx/port/main/linkerscript/application.dld.in`, section `.checksum` |
| Reference CRC, written after the link | `tools/romCheckImage.py` (`inject`, `check`), called by `cmake/NxRomCheck.cmake` |
| Plausibility of the list | `include/safeMemory/RomCheckList.h` (`checkList`, `feedAreas`, `Image`) |
| Pass and re-check | `include/safeMemory/RomCheck.h`, `src/safeMemory/RomCheck.cpp` |
| Flash write indicator | `include/safeMemory/RomCheckFlashWrites.h`, `src/romCheckFlashWrites/RomCheckFlashWrites.cpp` (CMake target `romCheckFlashWrites`) |

One **pass** is: the known-answer test of the CRC unit (five fixed words, expected `0x498CFAA3`, the value the Python
model of `romCheckImage.py` gives), then the plausibility of the list, then the CRC-32 through the CRC unit over the
areas, compared with `__checksum_result`. A mismatch, an implausible list, a failed known-answer test and a pass kept
off by flash writes for more than one period are all reported as `ROM_CHECK_FAILURE` (safe state S1 held).

**When a pass runs.** `RomCheck::init()` (from `SafetyManager::init()`) requests the first pass. `RomCheck::idle()`
runs it when it is due; it is called from the idle context only - once by the component `romcheck` of the board port
(lifecycle level 2) and in every round of the idle handler of the application (`ecu/application` `app.cpp`). After
the first pass the next is due when `modules.safeMemory.rom_check_period_ms` has passed since the start of the
previous one (0 = once per start). The pass runs at the lowest priority without a lock and outside the safety shell;
every supervised task preempts it. Only the bookkeeping at the end of a pass and the report enter the safety shell.

**Flash writes.** Every code that writes a flash module (the NvM through its memory, a later in-application update)
brackets the write with `safety::FlashWriteScope`. While a write is open, no pass starts; a pass during which a write
began is discarded without a report and repeated. A running flash command (`FTFC FSTAT[CCIF]` clear) counts as a
write. The indicator lies in ordinary RAM (its own library, whose archive name does not match the protected sections
`*libsafe*` of the linker script), so any task may update it. A due pass that could not run for longer than one period
is reported (`ROMCHECK_OVERDUE`), so a writer that never ends its write cannot switch the check off silently; with
period 0 there is no such deadline.

**Area with its own reference (SYS-91).** Flash content that may be written without a build - the calibration
segment of `memory.calibration` - is not in the list: the build reference would hold the ECU before the calibration
start-up check could apply `on_invalid`. The linker template marks its region (`__rom_check_own_area_start/_end`),
`romCheckImage.py` accepts image content outside the list only there, and the owner registers its check with
`RomCheck::setOwnAreaCheck()` (the integration in `ecu/application` `app.cpp`: `calibration::recheckSegment`, header
and values against the CRCs of the CALV header). Every pass calls it after the list, with the same period and flash
write rules; a deviation is `ROMCHECK_OWN_AREA_FAILED`, reported as `ROM_CHECK_FAILURE` like the rest.

**The CRC unit is reserved for SM-14.** No other code of the repository uses it; a second user would have to be
serialised with the pass.

The check is only compiled into an image whose reference the build wrote (`NX_ROM_CHECK_IMAGE`, set by
`nx_rom_check_image()` for its consumers `main` and `app.ecu`).

## Latent-fault test of the RAM ECC (SM-55)

`safe_memory::eccLatentTestAtStart()` (`EccLatentTest.h`), called once per start by `main()` of the family port after
the reset-loop limit and before the BSP. Per ECC channel of the derivative (S32K14x: SRAM_L and SRAM_U; S32K11x:
SRAM_U) the error injection module inverts one data bit, then two, on a read of a reserved word in that array
(`nxEccLatentTestWordFirst` in the hot bss, `nxEccLatentTestWordLast` in the no-init RAM). Expected: the single-bit
read returns the stored pattern and the ERM records a correction at the word's address; the double-bit read is
recorded as non-correctable (not on the S32K11x, whose Cortex-M0+ takes it as a hard fault). The EIM is off and the
ERM record cleared afterwards, the channel's ERM interrupt enables are masked during the test. A failure is
`INTEGRITY_EVENT_DMA_ECC_ERROR` with context bit 3 (`ECC_SOURCE_LATENT_TEST`), failed checks in bits 17..8, channel
in bits 27..24. The injected read (`eimInjectedRead`, `EccLatentTestTarget.cpp`) is register-only with all
interrupts and faults masked. The flash ECC has no injection module and is not tested (`fmea.md` SM-55).

## Configuration

Project (`project.yaml`, [modules reference](../../../../../../doc/reference/modules.md#m-s32k1xx-safememory)):

- `modules.safeMemory.rom_check_period_ms`, default 60000, range 0..86400000 ms - start to start of two passes;
  0 = once per start. A value other than 0 has to be longer than the longest flash write of the ECU (it is the
  deadline for postponed passes) and at most the latent-fault interval the integrator needs (AoU-25). Generated into
  `integration/safeMemoryConfiguration.h` as `NX_MODULE_SAFEMEMORY_PARAM_ROM_CHECK_PERIOD_MS`.

Duration of one pass: about 3 … 6 ms computed for 43 049 words at 80 MHz (`fmea.md` 5.1), not measured yet (B1).

## Verification

Host (`tools/test -k <name>`): `rom-check` (the real `RomCheck.cpp` against a register model of the CRC unit and the
FTFC status, own test profile with a period of 2500 ms), `rom-check-mutations` (21 built-in faults, slow),
`rom-check-image` (post-link step), `nvm-flash-write-bracket` (the NvM side of the suspension, `ecu/application`),
`safe-memory-ecc`, `bswgen-mpu`; ECC latent-fault test: `safe-memory-ecc-latent`, `safe-memory-ecc-latent-s32k11x`,
`safe-memory-ecc-latent-mutations` (slow), `safe-memory-ecc-latent-elf` (demo ELF). Board: first pass 2026-09-21
(eighth part); re-check and suspension open (B9); ECC latent-fault test open (SYS-73).
