<!-- SPDX-License-Identifier: Apache-2.0 -->

# What the AUTOSAR R25-11 machine-readable models give neXtBSW

Status: 2026-10-01 (**ARCH-62**). Verification: **mock/analysis** (documents read on a workstation, nothing built,
nothing flashed). User, 2026-09-23: "later, for the further development of neXtBSW, but no priority". R1 scope
(user, 2026-09-30): requirements basis and AUTOSAR proximity are a *must*, so this page records what the survey in
[`autosar-standard-set.md`](autosar-standard-set.md) could not: the result of actually opening the two models and a
few specifications.

Rules of [`autosar-standard-set.md`](autosar-standard-set.md) section 4 apply to every line below: nothing from the
release is checked in, nothing is quoted; evidence is document name, release and (where useful) the module and
parameter name. Parameter names and numeric limits are facts of an interface and are used as such. The prose
descriptions in the files are not, and nothing here reproduces them.

## 1. Summary

| Question | Answer |
|---|---|
| Can `tools/nxconfig` or the Studio **take over** ECUC definitions (as schema, as generated models)? | **No.** The definitions are module-by-module AUTOSAR configuration for AUTOSAR modules we do not have; the ranges are generic (mostly the full 32-bit range); they carry no derivative or board data and no cross-parameter rules. Taking them over would also drag AUTOSAR variant machinery in (decision D4 stays: YAML + pydantic). |
| Is there anything to **gain**? | **Yes, three modest things:** (a) a *name and range cross-check* for the parameters that have an AUTOSAR counterpart (diagnostics, NvM, CAN timing, watchdog supervision), (b) a *vocabulary map* so that an AUTOSAR-aware reader finds our parameter, (c) *machine evidence* for the claims of the survey page, which were made from the index only. Section 6 lists proposals. |
| Does the survey page hold? | **Mostly.** Four statements needed correction or a footnote (section 3); the main one: Fee, Ea and MemIf still exist in R25-11 and sit on MemAcc, so the memory stack is NvM -> Fee/Ea -> MemAcc -> Mem, not NvM -> MemAcc. |
| E2E and CRC against R25-11 | Nothing that touches our P01/P02/P11 implementation changed (section 4). |

## 2. What was opened, and what is in it

Both archives come from the direct-link scheme of [`autosar-standard-set.md`](autosar-standard-set.md) section 1, were
unpacked **outside the repository** and are not checked in. SHA-256 of the downloads of 2026-10-01:

```
66a7cf67c2190c6ece9c1bba271c02f885d6757e4cfcc3d6ac7f9868db4ba12a  AUTOSAR_CP_MOD_ECUConfigurationParameters.zip
03ff29e16e6d46f7d4c3fc1962c0d347e15a50c3b19bacac54573e2fe738aed2  AUTOSAR_CP_MOD_BSWUMLModel.zip
```

| Model | Content | Use for neXtBSW |
|---|---|---|
| ECUConfigurationParameters (one 19 MB ARXML, definitions only) | 112 modules, 1920 parameter containers; per parameter short name, type, multiplicity, minimum and maximum, rarely a default, enumeration literals, reference destination and configuration class. **No** units (only in the description text), no rule between two parameters, no value of a real device | name and range cross-check (section 5), vocabulary; no schema takeover |
| BSWUMLModel (84 MB Enterprise Architect repository, a SQLite file) | about 2540 interfaces, 165 distinct modules, error classes, client-server operations | none for `tools/nxconfig` or the Studio; at most a reference when a module is implemented (the SWS PDF says the same more readably) |

Module sizes relevant to neXtBSW (containers): Dcm 192, Dem 66, Os 41, Rte 47, Com 30, SecOC 23, CanIf 19, WdgM 19, Can
17, Xcp 14, CanTSyn 13, StbM 12, Mcu 9, NvM 9, Wdg 8, CorTst 7, RamTst 7, FlsTst 6, Mem 5, MemAcc 4. The UML model has
`Mem`, `MemAcc`, `MemIf`, `Fee`, `Ea`, `NvM`, `E2E`, `SecOC`, `CanTSyn`, `Com`, and no `Fls`, `Eep`, `LdCom` or hardware
test manager.

## 3. Cross-check of the survey page (what R25-11 changed)

The survey said that no specification had been read. These are the claims of its section 3 and the brief of this
task, checked against the ECUC file (and, for E2E/CRC, the PDFs).

| Claim | Result |
|---|---|
| Fls and Eep removed | **Confirmed.** Neither module exists in the parameter definitions or in the UML model. |
| "Memory stack is now Mem/MemAcc" | **Needs a footnote.** `Fee`, `Ea` and `MemIf` are still there. `Fee` and `Ea` reference a MemAcc address area, and the NvM block descriptor points at an Ea or Fee block (choice container). The stack is NvM -> Ea/Fee -> MemAcc -> Mem (`MemIf` selects between at most two devices). What disappeared are the *driver-specific* modules that sat below Fee/Ea. For NVM-* the relevant shape is `Mem`'s sector batch (erase sector size, write page size, sector count, start address, minimum read size, specified erase cycles, optional burst sizes) and `MemAcc`'s address areas with retry counts and a priority. |
| LdCom merged into Com | **Confirmed.** No `LdCom` module; `Com` has a large-data switch on module level and per I-PDU. For COM-*: Com also carries per-signal timeout, first timeout, invalid-data and timeout actions (none, replace, substitute) and the filter algorithms (always, masked-new-differs, new-is-within and others). |
| SecOC and CAN time sync "now in Foundation, no longer in CP" | **Wrong as worded.** The *protocol* (PRS, RS) moved to Foundation, but `SecOC` (23 containers) and `CanTSyn` (13) are still CP modules with CP configuration, and the SWS documents are still in the CP list. What moved is where the wire protocol is specified. |
| HWTestManager obsolete | **Confirmed**, and machine-readable: the ECUC file tags module `HTMSS` `obsolete`. The *standardised test modules that stay* are `CorTst` (core: ALU, registers, cache, MPU, interrupt, address, memory interface), `RamTst` (March, Galpat, checkerboard and further algorithms, with block lists), `FlsTst`. For the safety argument the start-up tests are therefore still expressible with standardised names, only the *manager* that scheduled them is gone. |
| Mcu, Wdg, Det still there | Yes. `WdgM` (alive, deadline and logical supervision) is the relevant vocabulary for program-flow monitoring. |

## 4. E2E and CRC against R25-11

Checked by reading the change histories and the P01/P02 data types of `AUTOSAR_FO_PRS_E2EProtocol`,
`AUTOSAR_CP_SWS_E2ELibrary` and `AUTOSAR_CP_SWS_CRCLibrary` (R25-11) against `libs/bsw/e2e` (basis R22-11). **Nothing
that touches P01, P02, P11 or the CRC routines in use changed**: the state machine rework matters only if
`E2E_SMCheck` is ever implemented ([`autosar.md`](autosar.md) section 2.2); profile 22 got a changed interface in
R24-11 (not implemented; add it only with the R24-11/R25-11 interface); the P02 configuration type still has an
`Offset` field, so the documented deviation of `e2e.md` is unchanged; the CRC check values are those of
`libs/bsw/e2e/test/host/E2eHostTest.c`. Not checked: the profile-specific flow charts line by line.

## 5. Where the ECUC definitions and our configuration overlap

Only the areas where `tools/nxconfig` (`model.py`) describes something that AUTOSAR also configures. Read as
"consistent" = same name idea and a compatible range; "ours is narrower" = deliberate or a candidate to document;
"gap" = a parameter AUTOSAR has that we do not. None of this is a finding against the code: the ranges of an
AUTOSAR parameter are generic limits, ours follow the implementation.

| Area | Ours (`model.py`) | AUTOSAR (module / parameter) | Observation |
|---|---|---|---|
| Diagnostic session id | `DiagSession.id` 0x01..0x7E | Dcm `DcmDspSessionLevel` 1..126 | consistent |
| Session timing | `p2_ms`, `p2_star_ms`, positive, no upper limit | Dcm `DcmDspSessionP2ServerMax` 0..1 s, `...P2StarServerMax` 0..100 s | ours has no upper bound (ARCH-68: warning, see [`autosar-ecuc-map.md`](autosar-ecuc-map.md) section 2); an optional *warning* above 1 s / 100 s would match the standard module, but the wire format of the standard allows more, so not an error |
| Security level | `DiagSecurityLevel.attempts` 1..255, `delay_ms`, `seed_bytes`/`key_bytes` 1..255 | Dcm `DcmDspSecurityNumAttDelay` 1..255, `...DelayTime` 0..65535 s, `...SeedSize`/`...KeySize` >= 1; at most 31 rows (`0..31`) | consistent; the row cap is a candidate check (ARCH-68) |
| DTC counter debounce | `DtcCounterDebounce`: failed 1..127, passed -128..-1, steps 1..255, jump values -128..127 | Dem `DemDebounceCounterBasedClass`: failed 1..32767, passed -32768..-1, steps 1..32767, jump values 16-bit; plus `DemDebounceBehavior` (freeze or reset) and `DemDebounceCounterStorage` | ours is narrower (8-bit counter, deliberate) and should say so; **gap:** freeze/reset behaviour while the enable condition is off, and storing the counter across power cycles |
| DTC time debounce | `DtcTimeDebounce`: `failed_ms`, `passed_ms` positive | Dem `DemDebounceTimeBasedClass`: 0.001..3600 s | ours has no upper bound; 3 600 000 ms would be the equivalent |
| DTC attributes | aging threshold 1..255, priority 1..255, `confirmation_threshold` 1..255, DTC number | Dem `DemAgingCycleCounterThreshold` 1..255, `DemDTCPriority` 1..255, `DemDtcValue` 256..16 777 214 (3 byte) | consistent; the DTC number range is taken from the ISO table in `diagdesc.py` and stays so |
| Fault memory size | `DiagFaultMemory.max_entries` 1..255, `snapshot_records_max`, `extended_records_max` | Dem `DemMaxNumberEventEntryPrimary` 1..255, `DemMaxNumberFreezeFrameRecords` 0..255, storage trigger (confirmed, FDC threshold, test failed) | consistent; **gap:** an explicit storage trigger |
| CAN bit timing | `CanUse.bitrate` in bit/s, `sample_point_percent` 50..95, optional `sjw_tq`, `prop_seg_tq` | Can `CanControllerBaudRate` in kbit/s (0..2000; FD 0..16000), `...PropSeg` 0..384, `...Seg1`, `...Seg2`, `...SyncJumpWidth` 0..255 | AUTOSAR configures segment lengths, we derive them from the sample point. The names `sjw_tq`/`prop_seg_tq` match. A tool that checks a project against the table of segment limits of a derivative is *not* covered by ECUC (the limits are in the derivative data) |
| CAN bus off | `CanBusOff`: `first_pause_ms`, `max_pause_ms` 0..65535, `discard_after` 0..255 | CanSM `CanSMBorTimeL1`/`...L2` 0..65.535 s, `CanSMBorCounterL1ToL2` 0..255, `CanSMEnableBusOffDelay` | the 16-bit millisecond range of ours is the same span as the standard's seconds with three decimals; the strategies differ (two levels with a counter versus growing pause) |
| NvM block | `NvmBlockUse`: `type`, `length`, `integrity` (qm / asil), write `shutdown`/`change`, `min_interval_ms`; block size at most 0x7FFF | NvM `NvMBlockDescriptor`: block id 2..65535, length 1..65535, management type (native, redundant, dataset), CRC type (8, 16, 32), read retries 0..7, write retries 0..7, write verification, job priority, write-once, write protection | ours is narrower by design (always two copies; CRC by integrity class); **gaps worth a look for NVM-\*:** retry counts, write verification, write-once; AUTOSAR reserves block ids 0 and 1 |
| Memory description | `MemoryRegion`: start, size, `sector_size` or `sectors`, `program_size` | Mem sector batch: start address, sector count, erase sector size, write page size, minimum read size, **specified erase cycles**, burst sizes | consistent in structure; **gap:** erase endurance per flash region (we have it only for the emulated EEPROM, `eeprom_emulation.endurance`) |
| Watchdog | derivative `LowPowerOscillator` frequency range; project/BSW supervision in `libs/bsw` | Wdg `WdgInitialTimeout`/`WdgMaxTimeout` 0..65.535 s, trigger mode toggle/window/both; WdgM alive supervision (expected indications, min/max margin, reference cycle), deadline and logical supervision | vocabulary for the program-flow monitoring of the safety concept; no data to import |
| E2E use | `E2eUse`: profile, data id, `max_delta_counter` | `E2E` has no ECUC module of its own in this file (E2E is configured through the transformer and the SWC description) | nothing to cross-check |

## 6. Proposals and their state

| # | Proposal | State |
|---|---|---|
| A | hand-kept vocabulary map "AUTOSAR: `Module.Container.Parameter`", marked "borrowed vocabulary, not conformance" ([`autosar.md`](autosar.md) section 0) | done (ARCH-68, [`autosar-ecuc-map.md`](autosar-ecuc-map.md)) |
| B | local cross-check script `tools/autosar_xcheck`, not run in CI; reads the ECUC ARXML from a path in an environment variable | done (ARCH-68); first run: 29 mapped parameters, all found, none outside the AUTOSAR limits; correction to section 5: Dcm allows at most **31** security rows (`0..31`) |
| C | optional bounds of section 5 as **warnings** in `tools/nxconfig/diagdesc.py` | done (ARCH-68) |
| D | take over the ECUC definitions as a generated schema or Studio form source | **rejected**: licence (reproduction only unmodified and for information), no derivative or board data, drags in variant handling; contradicts D4 |
| E | ECUC value export from the YAML for customers with an AUTOSAR tool chain | not proposed; goes against the standing "no ARXML configuration" line of [`README.md`](README.md); recorded so the option is not lost |

## 7. Hints for the open work areas

Taken from section 3 and 5; each is a pointer for the task named, not a requirement.

- **NVM-\***: read `Mem` and `MemAcc` for the flash side and `Ea`/`Fee` for the block layer; the ECUC parameters of
  section 5 (retries, verification, write-once) are the checklist of what a block manager usually offers. The
  `MemIf` device count is at most two, which fits "emulated EEPROM + data flash" without a general device list.
- **COM-\***: if a Com-like layer is ever added, follow the R25-11 shape (one module, large data as a switch, signal
  timeout and invalid-data actions), not the separate LdCom. The qualifier concept of
  [`signal-qualifier.md`](signal-qualifier.md) already covers timeout and validity at signal level; compare when
  that page is next touched.
- **SYS-\*/safety**: name start-up tests with `CorTst`, `RamTst` and `FlsTst` vocabulary where it fits; do **not**
  plan against a hardware test *manager*. WdgM vocabulary for alive/deadline/logical supervision.
- **Security (SecOC) and time sync**: when taken up, read the **Foundation** PRS for the protocol and the **CP** SWS
  for the module; both exist.
- **E2E**: see section 4; add P22 only with the R24-11/R25-11 interface.

## 8. How to repeat the analysis

The name and range comparison of section 5 is repeatable with `tools/autosar_xcheck` (see
[`autosar-ecuc-map.md`](autosar-ecuc-map.md) section 3). The counts of section 2 come from a plain walk of the ARXML
tree (`xml.etree`), the `.qea` as a SQLite file (`t_object`, `t_package`, stereotype `module`) and `pypdf` text
extraction for the change histories. Not covered: the other CP archives and the template specifications (the
ECUConfiguration TPS would be the place if proposal E is ever reopened), the Foundation and Adaptive sets, and every
adoption still needs the check against the document itself ([`autosar-standard-set.md`](autosar-standard-set.md)).
