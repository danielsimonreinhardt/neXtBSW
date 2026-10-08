# bspCsec – CSEc of the S32K1 family

Tasks NVM-13 and NVM-17. Family code of `platforms/s32k1xx` (architecture README section 6; place named in
[CSEc concept](../../../../doc/architecture/csec-concept.md) section 7.1). Today it holds exactly one thing: the
**way back to the factory state** after a `PGMPART` with a CSEc key space. The neutral crypto contract
(`libs/bsp/bspCrypto`, concept 7.1) is not part of it. The console command that drives it (`csec`, NVM-17) lives in
the family port (`platforms/s32k1xx/port/main/src/systems/CsecCommand.cpp`) and exists only in a test build.

> **Status: host-verified only (mock).** No CSEc command has completed on a device yet. The first board run
> (2026-09-30, NVM-07) ended in a hard fault before the first command - wrong parameter RAM address, fixed with NVM-21
> (section below). The board proof is part of **NVM-07**, with the user at the board, never unattended.

## Why it exists

A `PGMPART` with key space (`FCCOB1 != 0`) blocks the mass erase (Erase All Blocks, Erase All Blocks Unsecure,
MDM-AP/J-Link unlock) – with or without a loaded key (CLAUDE.md, decision `csec-pgmpart-rueckweg` of 2026-09-29;
[RM13] 36.5.11.14, Tables 36-56/36-60; [AN5401] 4.5). The only way back is the SHE debug authentication with the
`MASTER_ECU_KEY`. It erases all keys **and** undoes the partition: after the next reset the FlexRAM is plain RAM
(`FCNFG.RAMRDY` 1), the FlexNVM is all data flash (`FCNFG.EEERDY` 0), and the mass erase works again.
If any key carries `WRITE_PROTECTION`, there is no way back, ever.

## The procedure

| Step | Device (`CsecRecovery`, console `csec`) | Workstation (`tools/csec`) |
|---|---|---|
| 1 (only if no `MASTER_ECU_KEY` is loaded) | `csec m1`/`m2`/`m3`, `csec loadkey LOADKEY-MASTER-ECU-KEY` → `loadMasterKey()` → `CMD_LOAD_KEY`, key slot 1, answers M4/M5 | `load-master --project <p>` computes M1..M3, the expected M4/M5 and prints the four console lines; `check-load` compares the answer |
| 2 | `csec rng` → request in no-init RAM, reset → at the next start, before the BSP and the safety task: `initRng()` → `CMD_INIT_RNG` (skipped if `FCSESTAT.RIN`); one line "csec: INIT_RNG at start: …" | – |
| 3 | `csec challenge` → `challenge()` → `CMD_GET_ID` (UID), `CMD_DBG_CHAL` (CHALLENGE); refused with `RNG_NOT_READY` while `RIN` is clear | – |
| 4 | `csec dbgauth <AUTH> DBGAUTH-<first 4 challenge bytes>` → `authorize()` → `CMD_DBG_AUTH` | `debug-auth --project <p> --challenge … --uid …` computes `AUTH = CMAC_K(CHALLENGE \| UID)`, `K = KDF(MASTER_ECU_KEY, DEBUG_KEY_C)` and prints the line |
| 5 | the safety task's period check resets the ECU right after a successful `DBG_AUTH` (see below) – that is the reset the procedure needs; `csec status` shows the journal (`DBG_AUTH done, error bits 0x0001, … ms`), `flexnvm show` shows `RAMRDY 1, EEERDY 0` | – |

Step 1 loads the `MASTER_ECU_KEY` into its **empty** slot: authorised by itself, authorising value all ones,
counter 1, UID 0 allowed ([RM13] 36.5.13.13 note 2, [AN5401] 4.2.1 and Appendix A). The device never sees the
`MASTER_ECU_KEY`, only M1..M3 and the authorisation. The key comes from the key store of the workstation (NVM-14,
`security.master_ecu_key` of the project, `keystore.load_master_ecu_key`), never from the repository or the command
line. No other `csec` command may come between `challenge` and `dbgauth` (any CSEc command voids the challenge; a
`DBG_AUTH` without it is refused by the CSEc with `ERC_SEQUENCE_ERROR` and erases nothing).

### Console command (test build only)

```text
csec status                               CSEc, FlexRAM, FCSESTAT, watchdogs, staged M1..M3, journal
csec m1 <32 hex>                          stage M1
csec m2 <64 hex>                          stage M2 (a flag in its first block is reported at once)
csec m3 <32 hex>                          stage M3
csec loadkey LOADKEY-MASTER-ECU-KEY       -> result, error bits, M4, M5 (compare with tools/csec check-load)
csec rng                                  arms INIT_RNG for the next start, resets
csec challenge                            -> UID, SREG, CHALLENGE, the word for dbgauth
csec dbgauth <32 hex> DBGAUTH-XXXXXXXX    -> result (usually cut off by the reset of step 5: see the journal)
```

The console line of OpenBSW holds 127 characters (`StdioConsoleInput`, `etl::string<128>`); M1..M3 with the word are
166, so they are staged. `tools/csec` prints exactly these lines; the longest (`csec m2 …`) has 72 characters, the
`dbgauth` line 62 (checked by `ConsoleTest` against the device command table). A staged set is used once: after
`loadkey` it is cleared, whatever the result.

The command exists only with the CMake option **`NX_FLEXNVM_PARTITION_COMMAND`** (default OFF) – the same test build
that can write the partition with its key space: a build that can create the key space can also undo it. The product
build (`-DNX_PRODUCT_BUILD=ON`, SYS-74) refuses the option, and `tools/elfcheck product` looks for the command in the
linked image (symbols `CsecRecovery`/`CsecCommand`/`csecLaunchFromRam` and its help texts; counter check against a
test build). Nothing calls `CsecRecovery` at start-up except the `INIT_RNG` that `csec rng` requested.

## Watchdogs and the time budget (NVM-17)

`CMD_INIT_RNG` may need several hundred ms ([RM13] 36.5.13.16), and `CMD_DBG_AUTH` erases the data flash, the
emulated EEPROM and the key store – the data sheet gives no time for it; the Erase Flash Block of the FlexNVM alone
takes up to 550 ms on a 64 KB part and 4250 ms on the 512 KB of the S32K148 ([DS] Table 35). Interrupts are suspended
for the whole command (no flash fetch may happen), so no task can serve a watchdog. Three things can reset the ECU
meanwhile or right after:

| What | Handling | Evidence |
|---|---|---|
| **MCU watchdog** (WDOG, family peripheral; demo 250 ms) | The RAM function refreshes it while it waits – each time its counter has passed half the time-out, or the window if one is set (a refresh inside the window would reset) – for at most the **budget** of the command, measured on the watchdog's own LPO counter: `INIT_RNG` 2 s, `DBG_AUTH` 20 s (above the longest flash erase of the family, Erase All Blocks S32K148 17 s), the others 1 s. A command that hangs longer still ends in the watchdog reset. A watchdog the plan cannot cover (not from the LPO, no 32-bit refresh `CS.CMD32EN`, a window without room) is refused before anything is written (`WATCHDOG`). | host test: 600 ms `INIT_RNG`, `DBG_AUTH` of 550 ms / 5 s / 19 s without reset; hanging `INIT_RNG`/`DBG_AUTH`/`LOAD_KEY` reset after 2000–2375 / 20000–20375 / 1000–1375 ms; window, prescaler, clock, `CMD32EN` |
| **Watchdog outside the MCU** (SBC) | Not the family module's: the console command asks `integration::externalWatchdogAllowanceMs()` and refuses a command whose budget is longer. With a UJA113x: unlimited when its watchdog does not run (Forced Normal, or autonomous in software development mode – the eval board), else the period minus one service cycle; 0 before the SBC started. Without SBC module: unlimited (only the MCU watchdog). | guard test: every launching branch checks first |
| **Safety task period check** (SM-04, reset) | It sees the gap afterwards. After `LOAD_KEY`, `GET_ID`, `DBG_CHAL` (ms) nothing happens. After `DBG_AUTH` it resets the ECU – the reset the procedure needs anyway; the result is in the **journal** (no-init RAM, written before the launch "running" and before the interrupts come back "done", error bits, duration). After `INIT_RNG` it would reset and clear `RIN` again – so `INIT_RNG` runs at start-up, before the safety task, on request of `csec rng` (request cleared before the command, so a reset during it does not repeat it). | host test: journal "done" when the interrupts come back, "running" after a reset during the command |

A reset **during** `DBG_AUTH` is the one thing to avoid; with the above, neither watchdog can cause it within 20 s.
The measured durations (`csec status`, journal, start-up line) are the numbers NVM-07 brings back from the board.

Consequence for a board whose SBC watchdog runs in Normal mode (not the eval board): `loadkey`/`challenge` need a
period of at least 1024 ms, `rng` 4096 ms, `dbgauth` is refused (the SBC's longest period is 4096 ms). Such a board
needs a way to switch the SBC watchdog off for the test build (driver `libs/bsw/sbcUja113x`) – open, reported with
NVM-17.

## CallSeq of `CMD_LOAD_KEY` (NVM-17)

The two figures of `CMD_LOAD_KEY` in [RM13] (36-42 input, 36-43 output; likewise 37-34/37-35 in the FTFM chapter)
show `0x01` in the CallSeq byte. The code writes **`0x00`**, for these reasons:

- CallSeq tells the CSEc whether a call is the **first** one of a command (0) or a **continuation** (1) that brings
  more data than the parameter RAM holds ([RM13] 36.5.13.3.1 "CSEc command header"; [AN5401] 3.2 and 3.2.2 say the
  same: 1 is only for later calls, when the data do not fit).
- The general notes of the CSEc interface state that a command whose first call carries CallSeq 1 is answered with a
  sequence error.
- In every other figure of the chapter the first call carries 0; 1 appears only on the continuation pages of the
  multi-page commands (ENC/DEC CBC/ECB, GENERATE/VERIFY_MAC). `CMD_LOAD_KEY` has no continuation: M1..M3 fit into
  pages 1–4, M4/M5 come back in pages 5–7 of the same call. `CMD_LOAD_PLAIN_KEY` even states in its text that
  CallSeq 1 causes a sequence error.
- [AN5401] 4.2.1 describes the load as "write M1..M3, then the command header" – one call.

So `0x01` in the two `LOAD_KEY` figures is taken as a documentation error (the output figure repeats the input
header). Risk if this is wrong: the CSEc answers `ERC_SEQUENCE_ERROR` and changes no key (a refused command writes
nothing) – the console shows it, nothing is lost, and the change would be one constant (`CALL_FIRST`) with a new
review. The host test checks CallSeq 0 in every header (`testCallSeqIsFirstCallEverywhere`, mutant "CallSeq 1").

## Safety rules, and how the code enforces them

| Rule | Enforced by |
|---|---|
| **No key ever gets `WRITE_PROTECTION`** (CLAUDE.md) | `loadMasterKey()` accepts only M1 byte 15 = `0x11` (slot 1 authorised by slot 1) and an M2 whose first block is `FIRST_LOAD_M2_BLOCK0`: the encryption of "counter 1, all flags 0" under `K1 = KDF(all ones, KEY_UPDATE_ENC_C)`. With CBC and IV 0 that block depends on nothing else, and the flags live only in it – every M2 with a flag set is refused before anything reaches the CSEc. The host tool has no parameter for flags at all. |
| Irreversible steps only on a typed confirmation | `LOAD_KEY`: `LOADKEY-MASTER-ECU-KEY`; `DBG_AUTH`: `DBGAUTH-` + the first 4 bytes of **this session's** challenge in upper-case hex (a line from another session or another board is refused). One `DBG_AUTH` attempt per challenge; any command between `DBG_CHAL` and `DBG_AUTH` voids the challenge. |
| Nothing on the wrong part or state | refused before the first parameter RAM write: no CSEc (`SIM_SDID` FEATURES bit 7), FlexRAM not the emulated EEPROM (`EEERDY` 0 – no key space, the mass erase works), core not in RUN mode (`SMC_PMSTAT`: no CSEc or flash command in HSRUN/VLPR, data sheet), a flash command running (`FSTAT.CCIF` 0), an MCU watchdog the plan cannot cover |
| No flash fetch while a command runs | the header write, the completion wait and the watchdog refresh run in `.launchFromRAM` (as OpenBSW's `EepromDriver`), interrupts suspended, code cache off; the RAM function calls nothing and has no time-out of its own (checked on the target object) |
| Not reachable by accident | library `bspCsecRecovery` is `EXCLUDE_FROM_ALL`, linked only inside `if (NX_FLEXNVM_PARTITION_COMMAND)` of the family port; only `CsecCommand` names it, and everything naming `CsecCommand` elsewhere sits behind `#ifdef NX_FLEXNVM_PARTITION_COMMAND` (guard tests); product build refuses the option, `tools/elfcheck product` checks the image |

## Register interface used

| What | Where |
|---|---|
| CSE_PRAM | NVM-21: **`0x1400_1000` on the S32K14x(W), `0x1400_0800` on the S32K11x** - right behind the FlexRAM, chosen from `SIM_SDID` SUBSERIES (`csec/CsecPramAddress.h`); 32 words, aligned 32-bit writes only; message byte 0 = bits 31:24 of its word (SVD `BYTE_0`) |
| header (word 0) | FuncID 31:24, FuncFormat 23:16 (0 = copy), CallSeq 15:8 (0 = first call, see above), KeyID 7:0; written last, starts the command |
| error bits | word 1 bits 31:16 (`ERC_NO_ERROR` = 0x0001 … `ERC_GENERAL_ERROR` = 0x0800) |
| `CMD_LOAD_KEY` 0x07 | M1 page 1, M2 pages 2–3, M3 page 4; M4 pages 5–6, M5 page 7 |
| `CMD_INIT_RNG` 0x0A, `CMD_GET_ID` 0x10, `CMD_DBG_CHAL` 0x12, `CMD_DBG_AUTH` 0x13 | challenge/authorisation page 1; GET_ID: UID bytes 32..46, SREG byte 47 |
| completion | `FTFC.FSTAT.CCIF` |
| MCU watchdog | `WDOG.CS` (EN, CLK = LPO, CMD32EN, WIN, PRES), `WDOG.CNT` (read: counter; write `0xB480A602`: refresh), `WDOG.TOVAL`, `WDOG.WIN` |
| RUN mode | `SMC.PMSTAT` = 0x01 |

## NVM-21: the hard fault of the board run 2026-09-30

`csec loadkey` on the partitioned eval board (S32K144, key space 20 slots, no key) ended in `HARD FAULT RESET`:
`CFSR 0x00000400` (BusFault `IMPRECISERR`, `BFAR` not valid), `HFSR 0x40000000` (forced), stacked `PC 0x0002856a`
in `writeBytes()`, `R0 = 4` (page 1: M1), `R2 = 16`, `R1` = M1 + 8, `LR` (the loop counter there) = 8,
`R12 = 0x14000014`. The disassembly of the image (`board0930-csec`) shows one `str.w` (32-bit) per word to
`[r12, #0x800]`: two words of M1 had been stored, to `0x1400_0810` and `0x1400_0814`, when the fault was taken.

Cause: the parameter RAM address. NVM-13 took `0x1400_0800` from the only SVD at hand, the **S32K116**'s. On the
S32K14x the `CSE_PRAM` is at `0x1400_1000`; `0x1400_0800` is FlexRAM offset `0x800` - with the partition the
**emulated EEPROM** ([MAP] sheet "Overview Memory Map", see `csec/CsecPramAddress.h`). The first 32-bit store became
an EEPROM record and cleared `CCIF`; the FlexRAM is not accessible until `CCIF` is set again ([RM13] 36.5.2.3), so
the second store was terminated with a transfer error - buffered, hence imprecise. The J-Link probe of the evening
wrote the same address without an error because it wrote **one** word (it also became an EEPROM record, read back as
`0x11223344`). Before it, the J-Link read of `0x1400_0800..0x1400_081F` showed `0xFFFFFFFF` (erased EEPROM) except
`0x0000_0000` at `0x810` - the first M1 word of the fault -, where the CSE_PRAM reads 0 after a reset ([RM13] 37.5.12)
and would not have kept a word across the reset.

Not the cause: access width (32-bit stores, disassembly), peripheral access rights (`0x1400_xxxx` is not behind the
AIPS; no PACR applies), MPU (region 0 grants the core nothing - `RGDAAC0 = 0xFF7C77D8`, M0UM 000 - but region 1,
`0x0000_0000` up to the protected RAM, grants it full access), clock (RUN, 80 MHz), order (data before the header, as
required - the header was never written).

What reached the part: one EEPROM record with M1 bytes 0-3 at EEPROM offset `0x810` (overwritten by the J-Link test
word `0x11223344`). M1 carries UID and slot numbers only - no secret; M2/M3 were never written. Offset `0x810` lies
outside the demo's NvM layout (712 bytes from offset 0) and outside the CSEc key space (the last 512 bytes); the
`DBG_AUTH` of the way back erases the FlexNVM anyway. No CSEc command was launched: `csec status` after the reset
shows no journal and the key store is still empty.

Fix: the address is chosen at run time from `SIM_SDID` SUBSERIES (4: `0x1400_1000`, 1: `0x1400_0800`, else
`UNKNOWN_PART`, nothing written) and handed to every access, the RAM function included. The host model answers only
at the address of the part it plays (S32K144, S32K116); `AddressGuardTest` checks the table against the FlexRAM region of
every derivative in `mcudb` (right behind it, never inside); six mutants cover the address.

## Tests

`test/test_csec.py` (tools/test `csec-s32k1xx`, `csec-target-object`): host test against a CSEc and watchdog model
(`test/host/CsecRecoveryHostTest.cpp`, simulated LPO time), 52 mutants, the CSE_PRAM address against `mcudb` (NVM-21), guards (who calls it, CMake option, start-up,
external watchdog check before every launch), console lines of `tools/csec` against the device command, and the object
compiled with the demo build's exact flags (`.launchFromRAM`, no call, no relocation). Host side:
`tools/csec/test_she.py` (`csec-host`). Product image: `tools/elfcheck/test/test_product.py` (`elfcheck-product`) and
`product_build.py` (`target-product`).

Sources: [RM13] S32K1xx Reference Manual Rev. 13 (36.5.2.3, 36.5.13, 37.5.11, 37.5.12, 40.3.6), [MAP]
`S32K1xx_Memory_Map.xlsx` (file attachment of [RM13], sheet "Overview Memory Map"), [DS] S32K1xx Data Sheet Rev. 13
(Table 35, flash command timing), [AN5401] Getting Started with CSEc Security Module Rev. 1
(`doc/hardware/s32k1xx/AN5401.pdf`), HIS SHE Functional Specification v1.1.
