# dem – fault memory (simplified AUTOSAR DEM)

Status: NVM-03 (2026-09-21), verified with host tests (mock); the board check is open.

The fault memory records diagnostic trouble codes (DTCs) with their status byte, counters, snapshot and extended
data, and keeps them over a power cycle through the NvM. Its scope is the decision of the user of 2026-09-21: a
simplified AUTOSAR DEM **without warning lamp and without OBD**. Everything that describes a DTC is configuration –
the diagnostic description of the project (`projects/<project>/diag.yaml`, ARCH-51) – and the code only implements
the behaviour. Terms and numbers follow ISO 14229-1 (2026), Annex D; the standard is licensed and is only cited by
clause number.

## Parts

| Part | Where | What |
|---|---|---|
| Core | `libs/bsw/dem/include/dem/Dem.h` | `dem::Manager<Lock>`: status byte, debouncing, conditions, cycles, confirmation, aging, entries, persistence image; header only, board and family neutral |
| Types and interfaces | `dem/DemTypes.h`, `dem/IFaultMemory.h` | configuration tables, `IFaultMemory` (readers, clear, 85, BSW event interface), `ISource`, `IDidReader`, `dem::registry` |
| Reporting side of a BSW monitor | `dem/EventReporter.h` (COM-46/NVM-08) | finds the DTC of an event name once a fault memory is registered, reports the level at every call (COM-48: a report discarded while 85 was off is made good by the next call), a failure seen between two calls (or before the fault memory ran) once; host tests `libs/bsw/canSystem/test/test_can_bus_off_report.py`, `ecu/application/src/systems/dem/test/test_event_reporter_dtc_setting.py` |
| Results of the start (SYS-113) | `dem/StartReport.h`, `dem/SafetyResetReport.h` | what a start found in a record that survived the reset: FAILED until the fault memory shows testFailedThisOperationCycle (enable conditions, time debouncing and 85 off cannot swallow it), then PASSED once (clearable in the same run); sources without a failure PASSED once; optional fallback source for a failure without a DTC of its own. `SafetyResetReport.h`: the cause of the safety reset before this start from the reset-loop record (SYS-43) - `safety.<event>` (number of `SafeSupervisor::Event`), `safety.mcu_watchdog` (0xFD), `safety.fault_exception` (0xFC), `safety.reset_other` (0xFB, fallback), `safety.reset_loop` (0xFF, two or more in a row); fed by the BSP task of the s32k1xx port (`platforms/s32k1xx/port/main/src/integration/SafetyResetReport.cpp`); host test `libs/bsw/dem/test/test_start_report.py` |
| Occurrences at run time (SYS-196) | `dem/OccurrenceReport.h` | a monitor whose fault is gone before the fault memory could sample it counts occurrences (`occurred()`); `update()` in the monitor's task hands every batch over as one failure: FAILED until the fault memory shows testFailed in this operation cycle (enable conditions, debouncing and 85 off cannot swallow it), then PASSED once - pending/confirmed, the entry with the snapshot of the first confirmation and the occurrence counter stay, 14 clears it for good; occurrences during a hand-over fold into the next one. First user: `safety.safe_off_take_back` (+0x05FA), the take-backs of foreign safe_off levels of the s32k1xx port (`platforms/s32k1xx/port/main/src/integration/SafeOffTakeBackRecord.h`, BSP task); host test `libs/bsw/dem/test/test_start_report.py` |
| Tables | generated `RteDem.h` (tools/rtegen) | `rte::demtables::CONFIG`: DTC table, snapshots, extended data, cycles, conditions |
| Sources | generated `rte::demSample/demCondition/demCycleState` | one sample per event and cycle under the lock of the RTE |
| Persistence | NvM blocks `DemEvents`, `DemEntries` (tools/rtegen) | derived from the description, never written in `rte.yaml`; write policy CHANGE |
| Integration | `ecu/application/src/systems/dem/DemRte.h`, `RteSystem.cpp` | start after the NvM, cycle in the NvM context, run-down, console `dem` |
| Data identifiers | `ecu/uds` `DiagDidReader` | snapshot and extended data identifiers through the read functions of service 22 |

## Behaviour

- **Sampling.** Every `fault_memory.cycle_ms` (default 10 ms) the fault memory takes one sample of every event. A
  sampled source is a port of the ECU description (`event.port`, a provided port or the value a required port reads)
  or the signal qualifier of a received message (`event.bsw: can.qlf.<Message>`). How the value becomes a result is
  `event.result`: `boolean` (true: failed), `qualifier` (`QLF_OK` passed, `QLF_DEBOUNCE` no result, every error
  failed), `status` (AUTOSAR event status 0..3) or `limit` (`failed_above`/`failed_below`). Left out, the RTE
  generator derives it (limit given, qualifier source, boolean port).
- **Reported events.** Any other `event.bsw` name is reported by a BSW module through
  `dem::registry.memory->report(findReportedEvent(name), result)` – from any task or interrupt, taken over in the
  next cycle, a failure reported and passed between two cycles is still sampled once. `rte.deadline` is reported by
  `RteSystem` (deadline violations of SYS-05) as a level (COM-47, `ecu/application/src/systems/dem/DeadlineReport.h`):
  PREFAILED while a task cycle or periodic runnable missed its deadline at its last check or a violation happened since
  the last cycle, PREPASSED once the deadlines are met again - the DTC's debouncing decides when it matures, and a clear
  stays cleared while the deadlines hold (before COM-47 the level stayed failed until the next start), `can.busoff.<BUS>` by the CAN system of the board port for every
  used bus (COM-46, `can/CanBusOffReport.h` of `libs/bsw/canSystem`: FAILED while bus-off, PASSED while on the bus),
  `safety.device_fault` by `RteSystem` while the safe supervisor holds safe-output groups in the degraded reaction
  (NVM-08, logging only). A monitor uses `dem::EventReporter` for it.
- **Sampled BSW sources.** `bsw.power.undervoltage`/`bsw.power.overvoltage` (IO-59/NVM-08) are sampled by the RTE
  from the BSW service `bsw.power` (role power_supply): without a limit the state of the provider (its own thresholds,
  project values of its module, e.g. `modules.<sbc>.undervoltage_mv`), with `failed_below`/`failed_above` (mV) the
  measured supply voltage; no reading (provider not started, lost, voltage 0) is no result. `bsw.power.ok` is also an
  enable condition. The fault memory **only records**: a safety reaction stays with the
  safe supervisor (SYS-19); testFailed is not a fail-safe reaction (D.2.1).
- **Debouncing and fault detection counter (D.6).** Counter based: `increment`/`decrement` per sample between
  `passed_threshold` and `failed_threshold`, with `jump_up`/`jump_down` (default 0: the first failed sample after
  passed ones moves the counter over zero, so the DTC is prefailed at once). Time based: the same raw result for
  `failed_ms`/`passed_ms`. The fault detection counter is scaled to -128..127; 19 14 (NVM-04) reports
  `0 < fdc < 127`. FAILED/PASSED of a status source mature at once.
- **Status byte (D.2.2).** Matured failure: bits 0, 1, 2, 5 set, 4 and 6 cleared; matured pass: bits 0, 4, 6 cleared.
  Bit 3 (confirmed) after `confirmation_threshold` operation cycles with a failure (D.2.1); the DTC is stored then
  (snapshot records and identifiers of the extended data read at that moment). Bit 7 (warning indicator) is never
  set; the availability mask is 0x7F.
- **Operation cycles.** `autostart: true` cycles start with the ECU and end at its run-down; `autostart: false`
  cycles follow a boolean provided port connected to `dem.cycle.<Cycle>`. Start: bit 1 cleared, bit 6 set,
  debouncing restarts. End: a DTC tested without failure loses bit 2 (pending); the confirmation counter restarts.
- **Aging (D.7).** A confirmed DTC counts the cycles of its aging cycle that ended tested and without failure; at
  `aging.threshold_cycles` it loses bit 3 and its entry. A failure resets the counter.
- **Enable conditions.** A DTC samples only while all its conditions hold (port, `ecu.state` = RUN,
  `ecu.state.<STATE>`); otherwise the debouncing stands.
- **ControlDTCSetting (85).** Off stops every status change, cycle transitions included, except clearing (D.2.1).
  `ecu/uds` hands the service state to the fault memory. A `report()` of a BSW monitor is discarded while it is
  off (COM-43): the level stays the one reported before, so nothing a monitor reports during the preprogramming step
  of a flash sequence becomes a DTC afterwards. Sampled events are simply not processed while it is off. The
  reporters of the BSW (`dem::EventReporter`, `rte.deadline`) report their level at every call, so after 85 on the
  fault memory holds the current level again one call later (COM-48), not the one of before 85 off.
- **Clearing (14).** `clear(0xFFFFFF)` or one DTC number: status 0x50, counters and debouncing zero, entry deleted.
- **Overflow.** `replace_oldest` displaces the least important (largest `priority` number), oldest entry that is
  not more important than the new DTC (the displaced DTC loses bit 3); `keep_oldest` keeps the entries. A DTC that
  cannot be stored is not confirmed and retries on its next new failure.
- **Persistence.** `DemEvents` (status and counters per DTC) and `DemEntries` (entries with stored data) are written
  by the NvM after a change, at most every `fault_memory.write_interval_ms` (0: only at the run-down), and at the
  run-down after the cycles ended. The record id of both blocks follows the DTC table and the entry size, so a
  changed description starts cleared instead of misreading old data. On a memory that is not prepared (FlexNVM not
  partitioned, the eval board today) both blocks keep their zero defaults and the fault memory works in RAM.

## Interface for the diagnostic services (NVM-04)

`dem::IFaultMemory` (`dem::registry.memory`): `dtcCount`, `dtcConfig`, `dtcInfo` (number, status, severity, fault
detection counter, counters, stored), `findDtc`, `countByStatusMask`, `readSnapshot`/`readExtended` (record data as
stored; the identifiers of a snapshot record are in `dtcConfig().snapshots`), `clear`, `setDtcSetting`. The DTC
format is ISO 14229-1 (format identifier 0x01, derived in `tools/nxconfig/diagdesc.py`).
Served by `ecu/uds` `FaultMemoryServices` (NVM-04): 19 01/02/03/04/06/0A/14 and 14 for all DTCs, one DTC or a
group (groups are not yet part of the description, NVM-09).

## Console

`dem status | list | data | clear [<dtc>] | cycle | setting <0|1>` (`ecu/application/src/systems/dem/DemConsole.h`;
like `nvm` it exists only with the generated `NX_RTE_HAS_DEM` and is therefore not listed in
`doc/reference/console.md` yet). `dem cycle` ends and restarts the running cycles (a power cycle at the desk, for
pending and aging) and is a test function.

## Tests

Host-Tests, Testnamen und Verifikationsstand: [Modulkatalog](../../../../doc/reference/modules.md#m-dem).

## Open

- Board check (DTCs of the demo project, persistence with a partitioned FlexNVM).
- Board check of the BSW reporters (bus-off, undervoltage) and of the safety reset causes (SYS-113). A section for
  DTCs in neXtBSW Studio.
- SYS-113 limits: the held safe states (ROM check, watchdog self-test, the hold at the reset-loop limit) end only
  with a power cycle, which clears the no-init record - no DTC after the next start; a start that resets again
  before the fault memory wrote its NvM blocks (write interval) loses the DTC of the reset before it.
