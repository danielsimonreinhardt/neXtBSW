<!-- SPDX-License-Identifier: Apache-2.0 -->

# neXtBSW parameters and their AUTOSAR ECUC counterparts

Status: 2026-10-02 (**ARCH-68**, proposals A to C of [`autosar-ecuc-evaluation.md`](autosar-ecuc-evaluation.md)
section 6). Verification: **mock/analysis** (cross-check run on a workstation against the R25-11 file, tests on
a synthetic excerpt; nothing built, nothing flashed).

**Borrowed vocabulary, not conformance** ([`autosar.md`](autosar.md) section 0). The table says: "if you know the
AUTOSAR parameter on the right, this is the neXtBSW parameter on the left". It does not say that a neXtBSW
configuration is an AUTOSAR configuration, and it reproduces no AUTOSAR text: only module, container and parameter
names (facts of an interface) and numeric limits.

The machine-readable source of this page is [`tools/autosar_xcheck/map.yaml`](../../tools/autosar_xcheck/map.yaml).
A host test checks that every entry of the map appears here and that every model field it names exists; the script
`tools/autosar_xcheck` checks the AUTOSAR side (section 3).

## 1. The map

Result column: the last run against the R25-11 definitions (SHA-256 of the archive in
[`autosar-ecuc-evaluation.md`](autosar-ecuc-evaluation.md) section 2). `=` same limits, `narrower` ours lies inside
the AUTOSAR limits (deliberate or implementation-driven), `no upper bound` ours has none where AUTOSAR has one,
`known` an accepted difference.

### Diagnostics (Dcm)

| neXtBSW (YAML path) | AUTOSAR `Module.Parameter` | Unit | Result |
|---|---|---|---|
| `diag.yaml sessions[].id` | `Dcm.DcmDspSessionLevel` | - | = (1..126) |
| `diag.yaml sessions[].p2_ms` | `Dcm.DcmDspSessionP2ServerMax` | ms / s | no upper bound (AUTOSAR 1 s); warning above 1000 ms |
| `diag.yaml sessions[].p2_star_ms` | `Dcm.DcmDspSessionP2StarServerMax` | ms / s | no upper bound (AUTOSAR 100 s); warning above 100 000 ms |
| `diag.yaml security[].level` | `Dcm.DcmDspSecurityLevel` | - | = (sub-function 0x01..0x7D is level 1..63) |
| `diag.yaml security[].attempts` | `Dcm.DcmDspSecurityNumAttDelay` | - | = (1..255) |
| `diag.yaml security[].delay_ms` | `Dcm.DcmDspSecurityDelayTime` | ms / s | no upper bound (AUTOSAR 65 535 s) |
| `diag.yaml security[].seed_bytes` | `Dcm.DcmDspSecuritySeedSize` | bytes | narrower (1..255) |
| `diag.yaml security[].key_bytes` | `Dcm.DcmDspSecurityKeySize` | bytes | narrower (1..255) |

The Dcm definition holds at most 31 session rows and 31 security rows (multiplicity `0..31`); more is a warning in
`diag.yaml`. (The first version of the evaluation page said 32 for the security rows; the multiplicity is
`0..31`.)

### Fault memory (Dem)

| neXtBSW (YAML path) | AUTOSAR `Module.Parameter` | Unit | Result |
|---|---|---|---|
| `diag.yaml dtcs[].debounce.counter.failed_threshold` | `Dem.DemDebounceCounterFailedThreshold` | counts | narrower (1..127, 8-bit counter) |
| `diag.yaml dtcs[].debounce.counter.passed_threshold` | `Dem.DemDebounceCounterPassedThreshold` | counts | narrower (-128..-1) |
| `diag.yaml dtcs[].debounce.counter.increment` | `Dem.DemDebounceCounterIncrementStepSize` | counts | narrower (1..255) |
| `diag.yaml dtcs[].debounce.counter.decrement` | `Dem.DemDebounceCounterDecrementStepSize` | counts | narrower (1..255) |
| `diag.yaml dtcs[].debounce.counter.jump_up` | `Dem.DemDebounceCounterJumpUpValue` | counts | narrower (-128..127) |
| `diag.yaml dtcs[].debounce.counter.jump_down` | `Dem.DemDebounceCounterJumpDownValue` | counts | narrower (-128..127) |
| `diag.yaml dtcs[].debounce.time.failed_ms` | `Dem.DemDebounceTimeFailedThreshold` | ms / s | no upper bound (AUTOSAR 3600 s); warning above 3 600 000 ms |
| `diag.yaml dtcs[].debounce.time.passed_ms` | `Dem.DemDebounceTimePassedThreshold` | ms / s | no upper bound (AUTOSAR 3600 s); warning above 3 600 000 ms |
| `diag.yaml dtcs[].aging.threshold_cycles` | `Dem.DemAgingCycleCounterThreshold` | cycles | = (1..255) |
| `diag.yaml dtcs[].priority` | `Dem.DemDTCPriority` | - | = (1..255) |
| `diag.yaml dtcs[].confirmation_threshold` | `Dem.DemEventConfirmationThreshold` | cycles | = (1..255) |
| `diag.yaml dtcs[].number` | `Dem.DemDtcValue` | - | known: the schema allows 0..0xFFFFFF, `diagdesc.py` checks against the ISO DTC table |
| `diag.yaml fault_memory.max_entries` | `Dem.DemMaxNumberEventEntryPrimary` | entries | = (1..255) |
| `diag.yaml fault_memory.snapshot_records_max` | `Dem.DemMaxNumberFreezeFrameRecords` | records | narrower (0..254) |

Gaps (AUTOSAR parameter exists, neXtBSW has none): debounce behaviour while the enable condition is off
(`DemDebounceBehavior`), storing the debounce counter across power cycles (`DemDebounceCounterStorage`), an explicit
storage trigger (`DemEventMemoryEntryStorageTrigger`).

### CAN (Can, CanSM)

| neXtBSW (YAML path) | AUTOSAR `Module.Parameter` | Unit | Result |
|---|---|---|---|
| `project.yaml can.<Bus>.bitrate` | `Can.CanControllerBaudRate` | bit/s / kbit/s | no upper bound (AUTOSAR 2000 kbit/s nominal); the real limit is in the derivative data |
| `project.yaml can.<Bus>.sjw_tq` | `Can.CanControllerSyncJumpWidth` | time quanta | no upper bound (AUTOSAR 255) |
| `project.yaml can.<Bus>.prop_seg_tq` | `Can.CanControllerPropSeg` | time quanta | no upper bound (AUTOSAR 384) |
| `project.yaml can.<Bus>.bus_off.first_pause_ms` | `CanSM.CanSMBorTimeL1` | ms / s | = (0..65.535 s) |
| `project.yaml can.<Bus>.bus_off.max_pause_ms` | `CanSM.CanSMBorTimeL2` | ms / s | = (0..65.535 s); strategy differs (growing pause versus two levels) |
| `project.yaml can.<Bus>.bus_off.discard_after` | `CanSM.CanSMBorCounterL1ToL2` | - | = (0..255) |

AUTOSAR configures the segment lengths; neXtBSW derives them from the sample point (`tools/nxconfig/clock.py`).

### Non-volatile memory (NvM)

| neXtBSW (YAML path) | AUTOSAR `Module.Parameter` | Unit | Result |
|---|---|---|---|
| `project.yaml nvm.blocks.<Block>.length` | `NvM.NvMNvBlockLength` | elements / bytes | narrower (ours counts elements, the block is limited to 0x7FFF bytes) |

Gaps worth a look for the NvM tasks: read and write retries (`NvMMaxNumOfReadRetries`, `NvMMaxNumOfWriteRetries`,
0..7), write verification (`NvMWriteVerification`), write protection (`NvMBlockWriteProt`), erase endurance per flash
region (`Mem.MemSpecifiedEraseCycles`).

## 2. Warnings in `diag.yaml` (proposal C)

`tools/nxconfig/diagdesc.py` reports, as **warnings** (never errors, so no existing project breaks), a value
above what the AUTOSAR definition accepts: `p2_ms` > 1000, `p2_star_ms` > 100 000, time debouncing > 3 600 000 ms,
more than 31 sessions or security levels. The wire format of ISO 14229 allows more, so a project above the limit is
valid; it is merely not portable to a standard module. The constants sit at the top of `diagdesc.py` and name the
AUTOSAR parameter they come from.

Tests: `AutosarUpperLimits` and `test_time_debouncing_above_one_hour_is_a_warning` in
`tools/nxconfig/test_diagdesc.py`.

## 3. Repeating the cross-check (proposal B)

```
set NXCONFIG_AUTOSAR_ECUC_ZIP=<download folder>\AUTOSAR_CP_MOD_ECUConfigurationParameters.zip
cd tools
..\.venv\Scripts\python -m autosar_xcheck
```

The ZIP is read in place (nothing is unpacked or copied). The script prints one line per map entry
(`[OK]`, `[NARROWER]`, `[UNBOUNDED]`, `[KNOWN]`, `[OUTSIDE]`, `[MISSING]`) with names and numbers, never description
text, and the SHA-256 of the archive. Exit code 1 means a parameter was renamed or removed in the release
(`MISSING`) or one of our limits left the AUTOSAR limit (`OUTSIDE`); 2 means no or an unreadable file. Our limits are
read from the pydantic model of `tools/nxconfig`, so they exist in one place. It is **not** part of CI (the file is
not in the repository); the host test `tools/autosar_xcheck/test_xcheck.py` uses a small synthetic excerpt.

When a new release comes out: download the archive, run the script, fix the map or the model for every `MISSING` and
`OUTSIDE`, record the release and the SHA-256 in [`autosar-ecuc-evaluation.md`](autosar-ecuc-evaluation.md).
