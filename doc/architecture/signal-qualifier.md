# Signal qualifiers for received messages (COM-03)

Status: 2026-09-15, decisions of the user (Q1–Q7) incorporated. Binding for COM-03 and COM-10.
Reference: [architecture target picture](README.md) (L3 ECU services, L4 RTE; configuration level project).

## Goal

For every received message a software component gets, in addition to the signal values, a **qualifier** that says
whether the values are valid. Timeout, counter and checksum checks run in the generated RTE, not in the model.
The old MBDT project (`SYSCONST.QLF_*`, BACE_lib `TimeoutDetection`, `CheckAliveSignal`, `BACE_CrcCheck`,
`GenerateQualifier`) had this logic in every model; the values of the constants stay the same, so that model logic
such as `Qlf == QLF_OK` still fits.

## Decisions

| # | Question | Decision |
|---|---|---|
| Q1 | Where is it checked? | In the RTE (BSW), tested once; the model contains no checking logic |
| Q2 | Representation | Own port: RTE target `can.qlf.<message>`, data type `uint8` |
| Q3 | Granularity | One qualifier per message |
| Q4 | Several faults | One value with precedence: bus > timeout > CRC > counter |
| Q5 | Signal value in the fault case | Configurable per message, default `hold` (last valid value), alternatively `init` (start value from the DBC) |
| Q6 | Checksum and counter | **AUTOSAR E2E profiles** (see below) |
| Q7 | Recovery | `QLF_OK` again after one valid message |

## Qualifier values

| Value | Name | Meaning |
|---|---|---|
| 0 | `QLF_DEBOUNCE` | not yet valid: after start-up until the first valid message, and while a fault is being debounced |
| 1 | `QLF_OK` | all checks passed |
| 2 | `QLF_ERROR_ALIVE` | E2E counter repeated or wrong sequence, for longer than `debounce_ms` |
| 4 | `QLF_ERROR_CRC` | E2E checksum wrong, for longer than `debounce_ms` |
| 8 | `QLF_ERROR_TIMEOUT` | no message for `timeout_ms` |
| 16 | `QLF_ERROR` | bus fault (bus-off, transceiver not ready): immediately, for all receive messages of the bus |
| 32 | `QLF_ERROR_RANGE` | measured value outside its valid range, for longer than `debounce_ms` (library block `NxSwcLib/SensorQualifier` in the model, RTE-07) |

Precedence for simultaneous faults: `QLF_ERROR` > `QLF_ERROR_TIMEOUT` > `QLF_ERROR_CRC` > `QLF_ERROR_ALIVE` >
`QLF_ERROR_RANGE`. The sensor qualifier (RTE-07) passes a source qualifier other than `QLF_OK` through unchanged
and checks the range only when the source is valid; a range violation is reported as `QLF_DEBOUNCE` during
`debounce_ms`, recovery is immediate. The numeric value is not the order of precedence.
The RTE generates the constants as `Rte_Qlf.h` (C) and as a MATLAB file for the models (`QLF_*`, same values).

## E2E profiles

The profiles for classic CAN are supported; CAN FD follows with COM-06:

| Profile | Checksum | Counter | Data ID | Use |
|---|---|---|---|---|
| P01 | CRC8 SAE J1850 (polynomial 0x1D) | 4 bit | 16 bit, mode BOTH / ALT / LOW / NIBBLE | classic CAN, widespread |
| P02 | CRC8H2F (polynomial 0x2F) | 4 bit | list with 16 entries (one per counter value) | classic CAN |
| P11 | CRC8 SAE J1850 | 4 bit | 16 bit, mode BOTH / NIBBLE | successor of P01 for CAN |

- **Library `libs/bsw/e2e` (COM-10):** C99, without an OpenBSW dependency, so that the RTE, tester tools
  (host build via Zig/Python) and later other stacks can use it. Functions per profile: `Protect` (transmit:
  increment the counter, insert the CRC) and `Check` (receive: status `OK`, `REPEATED`, `WRONGSEQUENCE`, `ERROR`,
  `NOTAVAILABLE`, `NONEWDATA`). Host tests against the check values of the specification (e.g. CRC8 SAE J1850 over
  "123456789" = 0x4B, CRC8H2F = 0xDF) and against a Python reference model that `tools/canflash`/tests can use too.
- **Mapping onto the qualifier:** `ERROR` → `QLF_ERROR_CRC`; `REPEATED`, `WRONGSEQUENCE` → `QLF_ERROR_ALIVE`;
  `NONEWDATA`/`NOTAVAILABLE` → timeout monitoring. Instead of the E2E state machine (window/counter), the time-based
  debouncing from the old project (`debounce_ms`) and recovery after one valid message (Q7) apply.
- **Position of the fields:** named via DBC signals, not via bit offsets by hand. rtegen derives the offsets and
  checks length (CRC 8 bit, counter 4 bit), byte alignment and overlap with payload signals.

## Configuration (project level, ECU description)

```yaml
can:
  qualifiers:
    PC_Command:
      timeout_ms: 1000                  # if not given: GenMsgTimeoutTime of the DBC, otherwise 3 x cycle
      debounce_ms: 200                  # debouncing for E2E faults (CRC and counter)
      on_error: hold                    # hold | init
      e2e:
        profile: P11
        data_id: 0x0100
        data_id_mode: NIBBLE            # P01/P11
        crc: PC_Crc                     # DBC signal
        counter: PC_Counter             # DBC signal
    EVB_Status:                         # transmit message: the RTE protects it automatically
      e2e: { profile: P11, data_id: 0x0200, data_id_mode: BOTH, crc: EVB_Crc, counter: EVB_Counter }

connections:
  MotorCtrl.SpeedCmd.SpeedCmd:  can.rx.PC_Command.Speed
  MotorCtrl.PcCommandQlf.Qlf:   can.qlf.PC_Command
```

- Receive messages without a `qualifiers` entry only get timeout monitoring; `can.qlf.<message>` can still be
  connected.
- Transmit messages with `e2e` get counter and CRC from the RTE; CRC and counter signals must not be connected to
  ports (rtegen rejects that).
- Board-neutral: none of this depends on board or derivative; bus faults come through the CAN transceiver role
  (`can_phy`) and the CAN controller of the respective bus.

## Added during implementation (COM-03, 2026-09-16)

Decisions Q1–Q7 remain unchanged; this section only lists additions and deviations that were needed during the
implementation in `tools/rtegen`.

- **`data_id_nibble`:** in data ID mode `NIBBLE` the high nibble of the data ID goes into the message. Its
  position is a DBC signal, like CRC and counter: `e2e.data_id_nibble: <signal>` (mandatory in this mode, forbidden
  in the other modes).
- **Fixed E2E parameters:** `e2e.max_delta_counter` is configurable (default 1). The RTE sets
  `MaxNoNewOrRepeatedData` to the saturation value (P01 14, P02 15), `MapStatusToSM` runs with
  `profileBehavior = true` (R4.2); the time-based debouncing replaces the state machine of the specification (as
  foreseen in the section E2E profiles). `SyncCounterInit` is 1 (COM-14): a counter with a wrong sequence becomes the
  new reference, a stuck counter stays `REPEATED`, the next message in sequence is `SYNC` (→ `E2E_P_OK`) and
  recovers. With 0, a stuck counter came back once as `OKSOMELOST` after `delta − 1` messages and recovered the
  qualifier for one cycle (board capture 2026-09-16).
- **`Check` only on reception:** the RTE calls `E2E_PxxCheck` only when a message has arrived, not with `NULL` in
  every task cycle. Reason: P01 and P02 increase the counter tolerance with every call; with a 10 ms task and
  100 ms messages it would accept any counter value. `NONEWDATA` is not needed; the qualifier does the time
  monitoring.
- **Precedence within one fault phase:** CRC and counter faults cannot occur at the same time (one check returns
  one status). What is remembered is which kinds of fault occurred since the last valid message; after
  `debounce_ms` the RTE reports `QLF_ERROR_CRC` if a CRC fault was among them, otherwise `QLF_ERROR_ALIVE`.
  The debounce time keeps running in real time from the first fault, not only on reception.
- **`on_error` applies to every receive message**, even without an `e2e` section. This changes the behaviour from
  COM-02: previously the signals always fell back to `GenSigStartValue` after a timeout; now `hold` is the default
  (`on_error: init` restores the old behaviour). Signal values are only taken over from **valid** messages.
- **`timeout_ms: 0`** switches off the time monitoring (like a DBC without `GenMsgTimeoutTime` and without a cycle).
- **Bus faults** are read by the RTE at the transceiver of the bus: `getState() != OPEN` (muted, closed – the CAN
  layer mutes when the `can_phy` role is not ready) or `getCANTransceiverState() != ACTIVE` (bus-off). As long as the
  RTE is not yet attached to the transceiver (CAN comes up in an earlier lifecycle level), this does not count as a
  fault; the messages stay `QLF_DEBOUNCE`.
  **Corrected with COM-12 (2026-09-16):** the earlier assumption that `CanFlex2Transceiver::cyclicTask()` is never
  called has been disproved - `open()` schedules it itself in the task `can` every 10 ms. What was wrong was the
  **value**: OpenBSW's `FlexCANDevice::getBusOffState()` reads FLTCONF with the NXP **setter** macro and therefore
  permanently reported bus-off (on the board the qualifier of every message was stuck at `QLF_ERROR`).
  `systems::CanSystem` overrides `getCANTransceiverState()` and reads the field correctly; OpenBSW stays unchanged.
  **Explicitly not a bus fault is `error passive`:** without a counterpart node, TEC on the eval board runs up to
  128, and the qualifier should then keep showing timeout and CRC. A bus fault is only `BUS_OFF`.
- **Qualifier buffer for every receive message:** `can.qlf.<message>` is always an RTE buffer, even without a
  port. It therefore appears in `rte signals` and is visible for board tests. Permitted port types are `uint8`
  (default of the decision), `uint16` and `uint32`; several ports may read the same qualifier.
- **Constants:** `Rte_Qlf.h` (C/C++, `QLF_*` as `Rte_QlfType` = `uint8_t`) and `Rte_Qlf.m` (MATLAB script,
  `QLF_* = uint8(...)`) are always generated, even without CAN, so that models can include them.
- **Counters in `rte::CAN_RX_STATUS`:** `qlf`, `debounceMs`, `errorMs`, `crcErrors`, `aliveErrors` and
  `startOnError` supplement the timeout fields; transmit messages get `protectErrors`.

## Extensibility

The pattern `<source>.qlf.<name>` is intended for other BSW sources (e.g. `adc.qlf.<channel>` for conversion faults,
values from device drivers). Each is implemented as a task of its own.

**Implemented for ADC sensors (IO-18, 2026-09-17):** `adc.qlf.<channel>` belongs to `adc.phys.<channel>` and uses the
same values: `QLF_OK` valid; `QLF_ERROR_RANGE` (32) at the ADC limits (raw value 0 or full scale), outside the
output range of an amplifier, with an open pull-up or outside the breakpoints of a load – the value is taken over
nevertheless (end value), immediately and without debouncing, because it is a property of the individual conversion;
`QLF_ERROR` (16) on a failed conversion, the value is held; `QLF_DEBOUNCE` only as the start value before the first
transfer. `QLF_ERROR_RANGE` was chosen instead of `QLF_ERROR` because according to RTE-07 the numeric value means
exactly "measured value outside its valid range"; a downstream `SensorQualifier` passes it through unchanged as a
source qualifier other than `QLF_OK`. Formulas and configuration: [README.md](README.md) section 4 "Sensor
characteristics on ADC channels".

## Tasks

- **COM-10** E2E library (P01, P02, P11) with host tests and Python reference, independent of the rest.
- **COM-03** Qualifiers in rtegen/RTE: configuration, checking, debouncing, precedence, `hold`/`init`, E2E protection
  on transmit, constants for C and MATLAB, SIL test with timeout, CRC and counter faults, board test with the
  CANcaseXL.
