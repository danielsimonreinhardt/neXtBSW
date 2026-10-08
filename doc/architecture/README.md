# neXtBSW – architecture target picture

Status: 2026-09-15, **ARCH-01**, accepted by the user on 2026-09-15 (all proposals D1–D10 in section 10).
Readers: the user as architect, the coordinator and agents. **Binding** for all tasks; deviations only after
consulting the user and with an entry in section 10.
Reading versions for outsiders (English, DOC-07, 2026-09-18): [`overview.md`](overview.md) (structure),
[`openbsw.md`](openbsw.md) (relationship to Eclipse OpenBSW), [`autosar.md`](autosar.md) (where AUTOSAR fits in). They
explain the current state and refer back here; this document remains the binding one.
Amended 2026-09-17 (decisions of the user, history in `CHANGELOG.md`): **ARCH-28** core profile Cortex-R and D11–D14
(audit [`non-cortex-m-audit.md`](non-cortex-m-audit.md)); **ARCH-31** platform rule (section 6), family port at
application level (section 8), D7 amended, D15 added (analysis [`openbsw-naehe.md`](openbsw-naehe.md)).

## 1. Goal and guiding principle

neXtBSW is a universally usable basic software for ARM-based automotive MCUs, put together by configuration (later
neXtBSW Studio). Applications are added as Simulink models (AUTOSAR Classic SWC) through a generated RTE.

- Core profiles: **ARM Cortex-M and Cortex-R, 32 bit, little endian** (D11). Big-endian derivatives (e.g. TI TMS570)
  are not part of the target picture; the little-endian assumption (record format, `Platform_Types.h`, `canflash`)
  stays valid.
- Target families: **focus S32K1**, specifically also **S32K3**, **STM32** and **rm4x** (TI Hercules RM4x, Cortex-R4F;
  proof target LAUNCHXL2-RM46, D13), in principle any automotive MCU with these core profiles.
- Core and architecture are configuration, just like the board: code distinguishes profiles through the generated
  architecture (`NX_CORE_ARCHITECTURE`), never through core names or compiler macros such as `__ARM_ARCH` (equal to 7
  for both Cortex-R4F and Cortex-M4F, ARCH-19; implementation ARCH-23/24).
- A board is a use case of the architecture, never its yardstick. The S32K144 eval board is the first example and
  must not trigger any architecture-relevant decisions.
- One repository: reusable code (drivers, services, generators, bootloader) stays here, even when it is developed on
  the eval board. What is separated is **data**: derivative, board, project.

Terms:

| Term | Meaning | Example |
|---|---|---|
| Family | MCU series with shared driver code | s32k1xx, s32k3xx, stm32, rm4x |
| Derivative | specific chip with package | S32K144 LQFP100, STM32G474RE, RM46L852 |
| Board | assembled PCB: derivative, crystal, parts, wiring | evalboard_s32k144, nucleo_g474re |
| Project | one ECU software on one board: functions used, buses, tasks, SWCs | evalboard_motor, evalboard_charger |
| Module | reusable building block with a manifest (driver, service) | sbcUja113x, highSideSwitch, canSystem |
| Role | capability that a module provides or needs | can_phy, power_supply, watchdog_trigger |

## 2. Starting point: what OpenBSW delivers

The foundation is Eclipse OpenBSW as an unchanged submodule (version `v0.1.0-677-g00e3cab1`, 2026-08-13).

**Carries weight and is used** (details in [`openbsw.md`](openbsw.md) section 2): the platforms `posix`, `s32k1xx`
(reference S32K148-EVB) and **`stm32`** (Nucleo F413ZH with bxCAN, Nucleo G474RE with FDCAN, so STM32 is not new ground),
FreeRTOS or ThreadX, the hardware-independent middleware (`async`, `lifecycle`, `cpp2can`, `docan`, `transport`, `uds`,
`logger`, console, `time`, `util`, `storage`, safety libraries, Ethernet/DoIP/SOME/IP) and a few neutral BSP interfaces
(`CanPhy`, `SystemTimer`, `IFlashDriver`, `IEepromDriver`, `IEcuPowerStateController`, `UartConcept`,
`DigitalInput`/`Output`/`OutputPwm`, `charInputOutput`).

**Gaps that neXtBSW has to close**

| Gap in OpenBSW | Consequence today |
|---|---|
| No neutral ADC, SPI, I2C, input capture; watchdog and clock only as same-named headers per platform | ADC code (`AnalogInput`) sits in the board configuration, SPI we added ourselves |
| Pin level differs per family (`bios::Io` on S32K1, `Gpio` on STM32) | Generated pin configuration is family-specific |
| Integration per board by hand: `main.cpp`, `StaticBsp`, `CanSystem`, ISRs, linker script, startup are copied per board (the STM32 port even writes LED and UART registers directly in `SystemInit`) | A second board means copying; board peculiarities creep into the start-up sequence |
| No configuration description, no generators | all values in hand-written headers |
| Implementation can only be swapped via same-named headers and CMake targets (static) | no checkable contract between interface and implementation |

neXtBSW therefore adds: complete MCU interface contracts, device drivers, a configuration model with generators,
generated integration, an RTE with Simulink connection, a bootloader and the Studio.

## 3. Layer model

```
 +------------------------------------------------------------------------------------+
 | L5  Application: SWCs from Simulink (swc/, projects/<p>/swc)                       |
 +------------------------------------------------------------------------------------+
 | L4  RTE (generated): ports, runnables, tasks, data consistency, BSW service ports  |
 +------------------------------------------------------------------------------------+
 | L3  ECU services (systems): communication (CAN, DoCAN, UDS), state/supply,         |
 |     watchdog manager, NvM, fault memory, console, measurement/calibration          |
 +--------------------------------------------+---------------------------------------+
 | L2  Device drivers/CDD (libs/bsw/<module>) |  Integration (generated per project): |
 |     SBC, gate driver, cell monitoring,     |  main, start-up order, lifecycle      |
 |     smart high-side switch, ...            |  levels, ISR table, role wiring,      |
 +--------------------------------------------+  memory layout                        |
 | L1  MCU abstraction: contracts libs/bsp/<class>,                                   |
 |     implementation platforms/<family>/bsp (OpenBSW or neXtBSW)                     |
 +------------------------------------------------------------------------------------+
 | L0  Hardware: derivative + board                                                   |
 +------------------------------------------------------------------------------------+
 Cross-cutting: async/RTOS, lifecycle, logger, safety (family-specific parts in platforms/<family>/safety)
 Separate:      bootloader (C, without OpenBSW), own HAL per family, same configuration source
```

Rules:

1. **Dependencies only point downwards.** L2 knows only L1 contracts, L3 only L1/L2 contracts and roles, L5 only the
   RTE.
2. **No layer knows a board.** Board knowledge lives exclusively in configuration data and in the integration code
   generated from it. Hand-written code under a board name is only permitted for justified exceptions (e.g. working
   around a PCB defect), as a module of its own with a manifest.
3. **Exchange mechanism:**
   - L1: one implementation per build, selected via the family (static, like OpenBSW). Every contract has a header
     in `libs/bsp/<class>` and a **contract test** (host) that every family implementation must pass.
   - Above L1: narrow virtual interfaces where several implementations in one build or mocks are needed (models:
     `ISpiDevice` in the SBC driver, `CanPhy` from OpenBSW).
4. **Roles instead of part names.** Services request roles, modules provide them. `CanSystem` needs the role
   `can_phy` for a bus; whether a UJA1131, a TJA1044 with STB pin or a dummy fills it is decided by the configuration
   (section 5).
5. **Hardware-independent code gets host tests** with a register model or mock; tests run against their own test
   profiles.

### L1 contracts (target)

| Class | Contract | Today |
|---|---|---|
| Digital inputs/outputs | `DigitalInput`/`Output` (OpenBSW) | present, pin level family-specific |
| PWM | `OutputPwm` (OpenBSW) + complementary pairs with dead time | pairs as `ComplementaryPwm` in the board configuration → family |
| ADC | new `libs/bsp/bspAdc` (raw value, conversion, internal channels, resolution from the derivative) | only a board copy of the OpenBSW example |
| Input capture | new | missing (IO-03) |
| SPI | `ISpiDevice` (neXtBSW) | present, master only LPSPI |
| I2C | new | missing (IO-09) |
| CAN controller | `ICanTransceiver` (OpenBSW) | FlexCAN, bxCAN, FDCAN present |
| CAN transceiver | `CanPhy` (OpenBSW), readiness via `getPhyErrorStatus` | `sbc::Uja113xCanPhy` and `can::PassiveCanPhy` implement it (ARCH-05) |
| UART | `UartConcept` (OpenBSW) | present |
| Flash, EEPROM | `IFlashDriver`, `IEepromDriver` (OpenBSW) | present, FlexNVM only S32K1 |
| Watchdog, reset reason, clock | new as a contract (today same-named headers per platform) | family-specific |
| System time | `SystemTimer` (OpenBSW) | present |

## 4. Configuration levels

Three levels, each with its own schema (`schema_version`), YAML files, and one shared loader without Qt
(`tools/nxconfig`) used alike by the generators, the command line and the Studio. Values are set exactly once;
everything derived is computed by the generator.

```
 Derivative database        Board                         Project
 (manufacturer facts,       (what is fitted/wired)        (what the ECU uses)
  read-only)                boards/<board>/board.yaml     projects/<p>/project.yaml
 mcudb/<family>/...               |                        + rte.yaml, *.dbc, swc
        \_________________________|___________________________/
                                  v
                    nxconfig: load, link levels, validate
                     (conflicts, limits, missing roles, messages with YAML path)
                                  v
        bswgen (L1 configuration per family) · intgen (integration, memory layout)
        rtegen (RTE, tasks) · dbcgen · blgen (bootloader board part) · a2lgen (later)
                                  v
                build/<project>/generated/...  (not checked in)
```

### Responsibility per value

| Value | Level | Reason |
|---|---|---|
| Flash/RAM size, sector size, peripheral instances, pin mux options, ADC resolution, PLL limits, core (e.g. Cortex-M0+/M4F/M7/M33, Cortex-R4F), interrupt controller (NVIC with priority bits, VIM with channel count and fixed channels) | Derivative | datasheet, the same for all boards |
| **Possible** clock sources and asynchronous outputs per peripheral instance, their maximum frequency (s32k1xx `PCCn.PCS`, `instances.<x>.clock` with `sources`, `divider`, `max_hz`) | Derivative | datasheet (manufacturer fact) |
| **Chosen** clock source per used peripheral instance (`peripheral_clocks.<instance>`) | Project | usage decision; without an entry the derivative's `default` applies (ARCH-18) |
| Derivative and package, crystal frequency, pin assignment, voltage dividers, ADC reference voltage, fitted parts and their connection (SPI, CS, INT pins), transceiver per CAN connection, pull-ups, debug interface | Board | property of the PCB |
| Sensor circuit in front of an ADC pin (shunt, gain, reference, pull-up, diode, k_ILIS/R_IS), supply rails, fitted yes/no (`adc[].sensor`, `rails`, `adc[].fitted`, IO-18) | Board | property of the PCB; the connected load (NTC characteristic, breakpoint table, `adc_loads`) is decided by the project |
| Hardware peculiarities of a part that cannot be changed (e.g. MTPNV state of the SBC, missing pull-ups) | Board | belongs to the assembled PCB |
| Power stage per PWM group: switching times, gate charge, R_DS(on), gate resistors of the PCB, driver propagation delays, V_DS short-circuit threshold (`pwm[].power_stage`, IO-24) | Board | property of the fitted power stage; dead time and dead-time margin (`deadtime_margin_ns`) are decided by the project |
| Short-circuit detection settings of a gate driver that are chosen over SPI (V_DS thresholds, filter, delay: `modules.<device>.short_*`, IO-35) | Project | a choice of the project like the dead time; the board keeps only what the circuit fixes (R_DS(on), a hard-wired detector in `power_stage.short_circuit`); nxconfig derives the resulting short-circuit current from both |
| Which peripherals are used, system clock, PWM frequency and dead time, bit rates, CAN IDs, UDS addresses, buses and roles per bus, tasks (number, priority, period, stack), stack sizes per exception mode (Cortex-R), SWCs and connections, monitoring thresholds, memory split bootloader/app/NvM, FlexNVM partitioning, safety outputs, test and expert functions | Project | decision of the respective ECU software |
| Toolchain path, CAN adapter, serial port, debugger serial number | Workstation (local, not checked in) | belongs to the computer, not to the project |

Consequences for today's entries: `pwm.clock_hz` is dropped (derived), `pwm.frequency_hz`/`deadtime_ns` and `safety`
move into the project, `exclusive_groups` is dropped in favour of the general conflict check,
`adc_reference.resolution_bits` comes from the derivative.

**Separate fact and decision, even within one value (ARCH-18, user 2026-09-16).** The peripheral clock shows the
pattern: which sources and asynchronous outputs a clock switch offers and how fast the output may run is a
manufacturer fact and lives in the derivative; *which* of them is used is decided by the project. So that a project
does not have to fill in every value, the derivative names a **default** (`default`) that applies as long as the
project chooses nothing – backed by evidence like any other derivative value. If there is no evidence for a default,
it is left out: a used instance without a project choice is then an error with file, line and YAML path, instead of
entering a guessed value. The same pattern applies to future values of this kind (pin pull, filter times, conversion
times).

### Power-stage facts and minimum dead time (IO-24, user 2026-09-16)

A complementary PWM group describes its power stage in the board under `pwm[].power_stage` (`switch`, `driver`, `gate`,
`short_circuit`; int32 in fixed units ns, nC, mΩ, mV, each group with `source`/`verify`; fields in
[`board.md`](../reference/board.md)). The project decides `pwm.<group>.deadtime_ns` and the margin `deadtime_margin_ns`.
`tools/nxconfig` (`powerstage.py`) derives:

```
k_on  = (R_G,int + R_on,board)  / (R_G,int + R_G,test)        R_G,int missing -> 0 (conservative)
k_off = (R_G,int + R_off,board) / (R_G,int + R_G,test)        without resistor data: k = 1

t_off  = (t_d(off) + t_f) * max(1, k_off)                     switch still conducts after the turn-off command
t_on   =  t_d(on)         * min(1, k_on)                      opposite switch starts conducting

t_dead,min = max(0, t_off - t_on)                             switches
           + max(0, t_drv,off - t_drv,on)                     driver propagation delays
           + deadtime_margin_ns                               safety margin (project)
```

Switching times are entered as the datasheet states them (maximum, otherwise typical); the spread belongs in the margin;
scaling to the PCB gate resistance only lengthens the dead time. If `deadtime_ns` is below the minimum, nxconfig reports
it at the YAML path `pwm.<group>.deadtime_ns`: an error if all facts used have a source and are not `verify: true`,
otherwise a warning; missing facts give a warning with the path, never an assumption. The same facts yield the
short-circuit current `V_DS,threshold / R_DS(on)`, the minimum blanking time `(t_d(on) + t_r) * max(1, k_on)` and the
average gate current `Q_G * f_PWM`.

### Sensor characteristics on ADC channels (IO-18, user 2026-09-16)

Decisions of the user (all recommendations adopted; fields in [`board.md`](../reference/board.md) and
[`project.md`](../reference/project.md)):

1. **Board = circuit, project = load.** The board describes the circuit in front of the pin (shunt, amplifier, divider,
   pull-up) and delivers the quantity at the connector; the project describes the connected load (NTC characteristic,
   breakpoint table).
2. **Signed integer in fixed units** (mV, mA, Ω, 0.1 °C, ‰), no float, at run time too (S32K116 has no FPU).
3. **Kinds.** Board `adc[].sensor.kind`: `linear`, `shunt_amplifier`, `divider`, `pullup`, `highside_is`; rails under
   `board.rails`. Project `adc_loads.<channel>.kind`: `ntc_beta` and `table`.
4. **Zero-point calibration in operation** stays a matter for the application; a BSW service follows later.
5. **Quality at the ADC limits:** raw value 0 or full scale, amplifier outside its output range, open pull-up or a board
   value outside the breakpoints give `QLF_ERROR_RANGE` (value 32, [signal-qualifier.md](signal-qualifier.md)); a failed
   conversion `QLF_ERROR`; otherwise `QLF_OK`.
6. **No assembly variants in the schema:** the board carries the fitted variant; circuits not fitted are marked
   `adc[].fitted: false`. `scale` and `sensor` both non-trivial is an error (the circuit is stated once); part facts with
   several users (`k_ILIS`/`R_IS` of a high-side switch) are stated once, at the sensor of its IS channel.

`tools/nxconfig` (`sensor.py`) derives a rational integer conversion for each used, fitted channel (U = raw · V_REFH /
(2^bits − 1), exact as a fraction, then reduced):

```
x      = min(raw, calc_high)
board  = round((a * x + b) / (c * x + d))       int64, rounds half away from zero; c * x + d > 0 up to calc_high
value  = board | interpolate(points, board)     load: breakpoints ascending in board value, ends held

scale            U * factor + offset_mv                             mV
linear           U * gain_num / gain_den + offset                   unit
divider          U * (r_top + r_bottom) / r_bottom                  mV
shunt_amplifier  (U - U_ref) * 10^6 * gain_den / (gain * R_shunt)    mA   (R_shunt in µΩ)
pullup           R_pu * U / (U_supply - U_diode - U)                 Ω    (calc_high: last code with denominator > 0)
highside_is      k_ILIS * U / R_IS                                  mA
ntc_beta         table from R(T) = R_n * exp(B * (1/T - 1/T_n)) from t_min to t_max in steps of step_ddegc
```

Checks run over **every** raw value (int64 intermediates, int32 result, strictly ascending breakpoints, the largest NTC
error against `max_error_ddegc` as a warning); every message with file, line and YAML path. `tools/bswgen` generates
`bsp/adc/analogSensorConfiguration.h` (`nxAdcPhys_<NAME>(raw, &quality)`, comment with formula and range, "UNCONFIRMED"
if a source or `verify` is missing) and `tools/rtegen` the targets `adc.phys.<NAME>` (sint32) and `adc.qlf.<NAME>`;
`adc.phys` without `adc.qlf` gives a warning.

### Validation (minimum scope)

- Pin, timer channel, peripheral instance used at most once; pin function possible according to the derivative.
- Every requested role filled exactly once (e.g. `can_phy` for every bus used).
- Clocks and timings reachable (PLL, bit timing, baud rate, PWM resolution, dead time).
- Chosen peripheral clocks permitted by the derivative (source, output) and within the output's limit; the instance
  is actually used by the project (ARCH-18).
- Memory split fits into the derivative; bootloader and app do not overlap.
- Messages with severity, text and YAML path (basis for Studio live checking, TOOL-07).

## 5. Modules, roles and generated integration

Every reusable module in `libs/bsw/<module>` (and every L1 implementation) gets a manifest `module.yaml`:

```yaml
module: sbcUja113x
kind: device_driver                 # mcu_driver | device_driver | service | library | contract (DOC-14)
requires:
  spi_device: 1                     # L1 contract
provides:                           # roles that a board part of this type can fill
  - can_phy                         # per integrated transceiver
  - power_supply                    # V1/V2, sleep, wake-up sources
  - watchdog_trigger                # external watchdog
  - early_init                      # optional hook before the MCU watchdog test (RCC compensation)
config_schema: schema.yaml          # project parameters (watchdog mode, V2, interrupts, ...)
lifecycle: { level: 1, task: bsp }
cmake_target: sbcUja113x
console: sbc                        # optional, expert functions marked
verification: hardware              # none | mock | hardware, with verification_note (DOC-14)
```

`library` (for example `e2e`, `autosarBase`) and `contract` (an L1 interface such as `bspSpi`) carry only the catalogue
fields (`module`, `kind`, `description`, `cmake_target`, `verification`); nxconfig and bswgen never treat them as
device drivers. The module catalogue [`doc/reference/modules.md`](../reference/modules.md) is generated from these
manifests.

A module that gives the application readable values declares them in `rte` (RTE-18), so a new driver becomes
connectable through a target `<prefix>.<Instance>.<value>` without a change to the generator or the Studio:

```yaml
# libs/bsw/gdTmc6200/module.yaml (excerpt)
rte:
  prefix: gd                        # target gd.<Device>.<value>; left out: the module name in lower case
  access: gate_driver               # generic "reading" contract (below), or the access code of an existing module
  instance: Device
  values:
    faults:  { types: [uint16, uint32], unit: "gd::fault bits, latched until the driver's clearFaults()" }
    enabled: { types: [boolean], unit: DRV_EN switched on by the driver }
    qlf:     { types: [uint8, uint16, uint32], field: quality, unit: "QLF_* of Rte_Qlf.h", qualifier: true }
```

A new driver uses the generic contract `access: reading`: the manifest names a header (`include`), a reading struct
(`reading`) and a reader function (`read`); the module's family integration defines `void
::integration::rte::<read>_<Instance>(<reading>& reading)` once per instance (it takes a consistent snapshot itself), and
the generated RTE calls it once per task and cycle that reads the instance. Three older access kinds (`gate_driver`,
`hall`, `input_capture`) stay code of `tools/rtegen`; their manifest only states which fields are connectable. `instances`
(default `devices`) picks the board devices of the module or the board `input_capture` entries the project uses. At most
one value per module may be the **qualifier** (COM-03 `QLF_*`). `rte` targets are inputs only: nothing switches a device
on through them.

The board says which part sits where and which roles it fills there; the project says which of that is used:

```yaml
# boards/evalboard_s32k144/board.yaml (excerpt, sketch)
devices:
  - name: SBC
    module: sbcUja113x
    part: UJA1131HW/FD/5V/4
    connect: { spi: LPSPI1, pcs: PTE1, int1: PTE12, int2: PTE3 }
    roles: { can_phy: [CAN0], power_supply: true, watchdog_trigger: true }
can:
  - { name: CAN0, controller: FlexCAN0, rx: PTC2, tx: PTC3, phy: SBC }
  - { name: CAN1, controller: FlexCAN2, rx: PTC16, tx: PTC17, phy: { part: SN65HVD1050, module: canPhyPassive } }

# projects/evalboard_motor/project.yaml (excerpt, sketch)
board: evalboard_s32k144
buses:
  - { name: VEH, can: CAN0, bitrate: 500000, dbc: EvalBoard.dbc, node: EVB }
diagnostics: { uds: { bus: VEH, request: 0x7E0, response: 0x7E8 } }
modules:
  SBC: { watchdog: autonomous, v2: on_normal_standby_reset }
tasks:
  - { name: app10ms, priority: 5, period_ms: 10, stack: 2048 }
```

From this the integration generator (`intgen`) produces `main`, `platformLifecycleAdd`, instances of the systems, the
wiring of the roles, ISR binding and priorities as well as the task list. A board without an SBC declares a passive
transceiver or one with an STB pin for its CAN connection; `power_supply` is then filled by an MCU-internal module
(low-power modes) or stays empty; no service changes.

Principle: what is generated is **wiring**, not logic. Logic lives in modules with host tests.

### Generated binding of a device driver (ARCH-52, ARCH-66)

How a device driver is bound to the system - pins and SPI device, system object, lifecycle registration, safe_off
adapter, RTE reader - is generated from its manifest as well (decision `arch52-vorschlag`, analysis in
[`treiber-anbindung-analyse.md`](treiber-anbindung-analyse.md)). The manifest describes the wiring, a small **trait**
next to the driver holds the few C++-specific lines, the **family** provides one binder per connection kind and
contract, and `tools/bswgen` writes the block per instance (`integration/moduleBinding.h`, family neutral):

```yaml
# libs/bsw/highSideSwitch/module.yaml (excerpt)
connect:
  in:  { kind: digital_output, safe_off: true }   # safe_off: switched off by the fault handlers, the halt deadman, S1
  den: { kind: digital_output }
  is:  { kind: analog_input }                      # converted with the board channel's characteristic (IO-21)
lifecycle: { level: 1, task: bsp }                 # read: the instance is registered in this level and task
console: instance                                  # component name = console group of the instance
binding:
  include: highSideSwitch/HighSideSwitchBinding.h  # the trait, in the module's include directory
  trait: ::highside::HighSideSwitchBinding
```

Further manifest fields of the binding: `connect.<x>.optional: true` (the board may leave the connection open; the
trait sees `Connect::<X>_WIRED`; never together with `safe_off`) and `max_instances` (checked at the project entry
that brings in one instance too many). nxconfig checks every field with a YAML path: a binding needs a device
driver with `lifecycle` in level 1 or 2 and a BSW task, only the contracts the family binders offer (`spi_device`),
RTE access only through the generic `reading` contract, and the trait header must exist.

| Part | Where | Content |
|---|---|---|
| Trait | `libs/bsw/<module>/include/...` (the module) | `System`, `config<I>()`/`valid()` from the generated instance description `I` (`Param::`, `Connect::`, `NAME`, `PART`, `COMPONENT`, `LABEL`, `PWM_BOUND`), `Parts<I, B>` (pins on the binders `B`, SPI device), `create()`, one access struct `SafeOff_<connection>` per safe_off connection (`off`, `enables`, `setLock`, `updateLock`), `rte()` with `access: reading`, SPI device facts (`SPI_FRAME_BITS`, `SPI_CPOL`, `SPI_CPHA`, `SPI_MAX_HZ`, `SPI_TIMEOUT_US`). Contract: `tools/bswgen/modules.py`, section "binding (ARCH-52)" |
| Family binders | `platforms/<family>/port/main/src/integration/ModuleBinders.h` | digital output/input, ADC channel with characteristic, SPI device on a board bus - one per connection kind and contract, never one per driver |
| Framework | `.../integration/ModuleBinding.h` | **one** generic safe_off adapter `SafeOffOutput<Access>` for the debug halt guard and the hold of S1, the lock `safeOffLock()` (S1 latch, and for a PWM-bound instance the degraded group) |
| Generat | `integration/moduleBinding.h`, `moduleBindingConfiguration.h`, `modules.cmake` | per instance the description, the `static_assert` on `valid()` with the YAML path, `Parts`, storage, the safe_off adapters, `addComponents(manager, level)`; `NX_BINDING_TARGETS`, `NX_BINDING_MODULE_DIRS` |

The family port includes the generat once (`Integration.cpp`), calls `addComponents()` in level 1 (right after `bsp`)
and level 2 (after CAN), puts the generated safe_off adapters into the list of the debug halt guard and the safe
state hold (checked against the safe outputs table, `NX_SAFE_OFF_OUTPUT_COUNT`) and links `${NX_BINDING_TARGETS}`;
the LPSPI master of the family is linked with the signal kind SPI (`NX_USE_SPI`), not with the SBC. A new driver with
a binding therefore needs **no line in the family port** (proof: the synthetic fourth driver of
`tools/bswgen/test/binding`).

State (2026-09-30): `highSideSwitch` is bound this way (ARCH-66; lockstep comparison with its former hand-written adapter
in `test/host/SafeOffEquivalenceHostTest.cpp`). Still written by hand in the family port: `sbcUja113x` (roles `can_phy`,
`power_supply`, `early_init`, ARCH-53) and `gdTmc6200` (a per-instance lock and "keep enable" in an active short, SYS-139;
the PWM group as duty source, IO-45; RTE values with the supervisor's bus verdict, SYS-29). The `add_subdirectory` of the
module directories from `NX_BINDING_MODULE_DIRS` belongs into the root `CMakeLists.txt` (today a fixed list there).

## 6. Platforms and families

| Family | Foundation | neXtBSW addition | State |
|---|---|---|---|
| s32k1xx | OpenBSW `platforms/s32k1xx` | LPSPI, ADC contract, complementary PWM, input capture, FlexIO I2C, derivatives 142/144/146/148 | in progress (eval board) |
| stm32 | OpenBSW `platforms/stm32` (F4 bxCAN, G4 FDCAN) | missing contracts (ADC, SPI, PWM, watchdog), further derivatives | after ARCH-10 |
| s32k3xx | no OpenBSW platform yet | own `platforms/s32k3xx` (Cortex-M7), driver source open | after ARCH-10 |
| rm4x | no OpenBSW platform | own, **OpenBSW-conformant** platform `platforms/rm4x/{3rdparty,bsp,etlImpl,hardFaultHandler,safety,unitTest,CMakeLists.txt}` (Cortex-R4F): startup in ARM state with mode stacks, VIM dispatch, RTI tick, DCAN, F021 flash from RAM; sources open until PLAT-44 (D14) | audit ARCH-19; preparation ARCH-23–27, BL-08, RTE-13; family PLAT-35–39, BL-09; board PLAT-40–43 |

- Own family extensions live in `platforms/<family>/bsp/<module>` in the OpenBSW layout; same-named OpenBSW modules
  are used, not duplicated. A family without an OpenBSW platform (rm4x) brings its complete platform in
  `platforms/<family>` (ARCH-25). Binding for this is the **platform rule** at the end of this section.
- Vendor libraries and sources (NXP RTD, STM32 HAL/LL, TI HALCoGen register headers, TI F021 flash API) only behind
  L1 contracts and only after a licence check (ARCH-10, for rm4x PLAT-44, D14).
- Toolchain: generic `arm-none-eabi-gcc` for all core profiles; core options (`-mcpu`, FPU) from the derivative
  database.
- Startup, vector table or **interrupt controller table**, linker script (for Cortex-R with stacks per exception
  mode) and MPU regions are family templates, filled from derivative and project:

  | Core profile | Controller | Table | Priority, RTOS boundary | Jump from the bootloader |
  |---|---|---|---|---|
  | Cortex-M | NVIC | vector table (word 0 stack pointer, then Thumb addresses), switchable via VTOR | rank → NVIC level; boundary BASEPRI (ARMv6-M: global lock) | VTOR to the application entry |
  | Cortex-R | VIM | eight instruction vectors at 0 (ARM state, no VTOR) + VIM channel table (handler per channel) | rank → channel order (priority = channel number); boundary: IRQ may call the RTOS, FIQ never | fixed forwarding table at the application entry (D12, section 7) |

  The rank remains a project term (section 4); the mapping onto the controller is derived by `nxconfig` and the
  family generator (ARCH-23/24).

### Platform rule: OpenBSW layout per family (binding, user 2026-09-17)

Decision of the user of 2026-09-17 (decision board: ARCH-30 F1/F3, RM46 option A) based on
[`openbsw-naehe.md`](openbsw-naehe.md) section 3. Yardstick: OpenBSW has **no** cross-family platform level – what
several platforms need either lives neutrally in `libs/` or is kept per platform.

**P1 – Layout.** Every family is an OpenBSW platform. `platforms/<family>` has the layout of
`external/openbsw/platforms/{s32k1xx,stm32}`: `bsp/<module>`, `3rdparty/<rtos-port>`, `etlImpl`, `hardFaultHandler`,
`safety`, `unitTest`, `CMakeLists.txt`; integrated through the hook `OPENBSW_PLATFORM`.

**P2 – Self-containment.** `platforms/<family>` plus `libs/` is sufficient. No platform module includes a module of
another platform, and none depends on `platforms/common`. What OpenBSW places per platform (RTOS port,
`hardFaultHandler`, startup, `etlImpl`, `safety`, `bsp/*`) stays per family – even if it then exists more than once.

**P3 – `platforms/common` (the checkable rule).** `platforms/common` contains **only modules that have no counterpart
in OpenBSW**. Check per directory `platforms/common/<module>`, without discretion:

1. Is there a module with the same purpose in `external/openbsw/platforms/<family>/`? → The module belongs **per
   family** in `platforms/<family>/<module>`, not in `common`.
2. Is there a module with the same purpose in `external/openbsw/libs/` (including `libs/3rdparty/`)? → It belongs
   **neutrally** in `libs/bsp` or `libs/bsw`, not in `common`.
3. Neither, and the part is neXtBSW's own (bound to the generator or the core profile)? → `platforms/common` is right.
4. Every deviation from 1–3 is an **exception** and is listed with reason and end condition in the list P4. Without
   an entry there it counts as an error.

**P4 – Inventory of `platforms/common` (list from [`openbsw-naehe.md`](openbsw-naehe.md) section 3, as of
2026-09-17):**

| Part under `platforms/common` | OpenBSW counterpart | Verdict according to P3 |
|---|---|---|
| `cmake/NxCoreArchitecture.cmake` – architecture (`NX_CORE_ARCHITECTURE`) from nxconfig | none (OpenBSW's CMake helpers live in the repository root `cmake/`) | stays (ARCH-25, C1); moving it to the root `cmake/` would be closer to OpenBSW, but is not a must |
| `interruptPriority`, `cortex-m/interruptPriority`, `cortex-r/interruptPriority` – ranks from the project mapped to NVIC levels or VIM channels | none (OpenBSW writes the levels as numbers in `main.cpp`) | stays (ARCH-24/26, C4) |
| `test/` – build tests per core profile without a board (`cortexm0plus`, `cortexr4f`) | `platforms/<family>/unitTest` has a different purpose (module tests of the platform) | stays (PLAT-10/35, C5) |
| `newlib` stubs (`_close/_lseek/_read/_write`) under `platforms/common/newlib` | none | stays; moved there with PLAT-66 (PLAT-45, T14) |
| `clockCheck` – start-up report of the clock check (`nxClockCheckReport`: one console line, text of the fault codes) | none (the OpenBSW clock drivers report nothing; the check `nxClockCheck()` and its contract `clock/nxClockCheck.h` are family code and live per family in `platforms/<family>/bsp/bspClock`, the contract byte-identical in every family) | stays (rule 3; PLAT-72, placed with PLAT-85). Only the family-neutral formatting lives here; no family module depends on it (P2) – the integration (`port/main`, the stm32 build test) links both |
| `3rdparty/freertosPort` – **selection** of the port by architecture | yes: OpenBSW selects in `platforms/<family>/CMakeLists.txt` | **exception (permanent):** only the switch stays central, because it depends on the generated output. The **ports themselves** live per family: `platforms/s32k1xx/3rdparty/freertos_cm0_sysTick`, `platforms/rm4x/3rdparty/freertos_crx` (ARCH-25, PLAT-35) |
| `3rdparty/cmsis/core_cm0plus.h` | yes: `external/openbsw/libs/3rdparty/cmsis` (contains only `core_cm4.h`) | **exception (temporary):** until the header is upstream (PLAT-53, C2); after that the copy is dropped |
| Hard fault handler (originally planned for `common` in ARCH-27) | yes: `platforms/<family>/hardFaultHandler` (separate for `s32k1xx` and `stm32` in OpenBSW) | **not into `common`:** assembler handlers stay per family; only the architecture-neutral dump layout with architecture ID and the reader become neutral `libs/bsw/faultDump` (ARCH-27, re-scoped) |

**P5 – Family port at application level.** The hand-written port (so far `platforms/<family>/port`, PLAT-23) lives at
application level as in OpenBSW: `ecu/platforms/<family>` – modelled on `referenceApp/platforms/<board>`.
`platforms/<family>` thus contains only the MCU level (ARCH-30 F1; implementation PLAT-54, then PLAT-55;
D7 in section 10 amended accordingly).

**P6 – rm4x as proof (RM46 option A).** `rm4x` is built in this layout from the start; the neXtBSW connection
(generated output, configuration, RTE) stays separate from it:

```
platforms/rm4x/
  CMakeLists.txt         integration via OPENBSW_PLATFORM, derivative options from mcudb
  3rdparty/freertos_crx  FreeRTOS port ARM_CRx_No_GIC (PLAT-35) - here, not under platforms/common
  bsp/<module>           bspMcu, bspClock, bspVim, bspRti, bspGio, bspSci, bspDcan, bspF021 (PLAT-36-38)
  etlImpl/               ETL profile of the platform
  hardFaultHandler/      Data Abort, Prefetch Abort, Undefined (ARMv7-R); dump layout from libs/bsw/faultDump
  safety/                MPU, watchdog (DWD/DWWD) and ESM parts of the family
  unitTest/              platform module tests in the OpenBSW way
```

Additional acceptance criterion of the family: the **OpenBSW `referenceApp` or its `unitTest` builds for RM46**
(PLAT-36/37/38, BL-09). The build tests `platforms/common/test/cortexr4f` may include the port from
`platforms/rm4x/3rdparty`; conversely, `platforms/rm4x` must not need anything from `platforms/common/test`.

## 7. Bootloader

- Stays lean in C without OpenBSW: core (`bootloader/core`: UDS, ISO-TP, image check) + HAL per family
  (`bootloader/platforms/<family>`: flash, CAN, clock, jump) + optional part hooks
  (`bootloader/devices/<part>`, e.g. switching on the SBC) + generated board/project part (CAN connection, pins,
  bit timing, IDs).
- **One source for the memory layout** (derivative + project): bootloader linker, app linker, validity record,
  boot request address, updater and `canflash` read the same generated values.
- Family-specific items such as the Flash Configuration Field (S32K1 at 0x400) belong in the family HAL, not in the
  core.
- The interface to the application is the **application entry**, not a vector table. What stands there and how an
  image is checked for plausibility is decided by the family (hooks in bootloader, updater and `canflash`, BL-08):
  Cortex-M sets VTOR; Cortex-R has no VTOR – the bootloader vectors forward **Undefined, SVC, Prefetch Abort and
  Data Abort** through a **fixed forwarding table at the application entry** (convention versioned like the record
  format), **IRQ/FIQ** go through the vector registers of the VIM and need no forwarding. The bootloader itself uses
  neither SVC nor aborts as long as the table is not in effect (D12, implementation BL-09).
- Compatibility: identifier and record format with a version field; a bootloader already flashed remains updatable.

## 8. Repository layout (target)

```
external/openbsw/             unchanged (submodule)
platforms/<family>/           MCU level in the layout of an OpenBSW platform (platform rule P1/P2, section 6):
  bsp/<module>                L1 additions and MCU drivers of the family
  3rdparty/<rtos-port>        RTOS port of the family (freertos_cm0_sysTick, freertos_crx) - never under common
  hardFaultHandler/           fault/abort handlers per family; layout and reader neutral in libs/bsw/faultDump
  etlImpl/ safety/ unitTest/  as in external/openbsw/platforms/<family>, plus CMakeLists.txt (OPENBSW_PLATFORM)
platforms/common/             ONLY neXtBSW's own parts without an OpenBSW counterpart (platform rule P3/P4):
  cmake/                      profile-neutral: architecture from nxconfig (NX_CORE_ARCHITECTURE)
  3rdparty/freertosPort/      only the port selection by architecture (armv6m, armv7m, armv7r);
                              the ports themselves live under platforms/<family>/3rdparty
  test/                       build tests per core without a board (cortexm0plus, Cortex-R4F: PLAT-35)
  cortex-m/                   NVIC interrupt ranks; CMSIS core header as a temporary exception (PLAT-53)
  cortex-r/                   CP15 accesses, VIM channel ranks
libs/bsp/<class>/             L1 contracts (neutral) + contract tests
libs/bsw/<module>/            device drivers and services with module.yaml, host tests (including faultDump)
ecu/                          neutral ECU framework: application, systems, uds, consoleCommands
ecu/platforms/<family>/       port template of the family at the OpenBSW location (PLAT-54, modelled on
                              referenceApp/platforms/<board>): bspConfiguration, safety, main (startUp.S,
                              bootHeader.S, linker script, ISRs, StaticBsp, BspSystem),
                              freeRtosCoreConfiguration - no board value and no derivative value in it
ecu/platforms/<board>/        board port: Options.cmake + board peculiarities and justified exceptions
                              (no port points to another, ecu/platforms/README.md)
bootloader/                   core/, platforms/<family>/, devices/<part>/, updater/
mcudb/<family>/               derivative database (manufacturer facts)
boards/<board>/               board.yaml, documentation, board notes (e.g. safety rules for the board)
projects/<project>/           project.yaml, rte.yaml, DBC, project-specific SWCs
swc/<component>/              reusable components and examples (DemoSwc)
tools/                        nxconfig, bswgen, intgen, rtegen, dbcgen, blgen, canflash, studio, todo
doc/architecture/             this document, decisions
```

Build: `cmake --preset <project>` (presets generated from the projects, or generic with
`-DNX_PROJECT=projects/<p>`); board, derivative and family follow from the project configuration. The Studio opens a
project through its folder.

On `ecu/platforms/` (**D7 amended 2026-09-17**, ARCH-30 F1; previously "`ecu/` without `platforms/`"): `ecu/platforms/`
stays and is the OpenBSW location for **both** port levels, like `referenceApp/platforms/<board>` in OpenBSW.

- `ecu/platforms/<family>/` is the **port template of the family** (today `platforms/<family>/port`, PLAT-23;
  move in PLAT-54, startup templates after that in PLAT-55). It contains no board value and no derivative value.
- `ecu/platforms/<board>/` contains only the **feature switches** (`Options.cmake`, whose target location according
  to section 9 row 2 is the project) and justified board exceptions. This board folder disappears once the switches
  are in the project (ARCH-04/05) and the integration is generated; until then it is the only place with a board name
  in the code.
- After the move, `platforms/<family>/` contains only the MCU level – exactly like `external/openbsw/platforms/*`.
- Module manifests are found in both port levels (`tools/nxconfig/config.py`, `port_module_dirs`).

The layout under `platforms/` is thus no longer a neXtBSW invention, but checkably the OpenBSW layout: platform rule
P1–P6 in section 6, review question 10 in section 11.

## 9. Today's board couplings and target location

Where the repository still deviates from the target picture (read the paths `…` as `platforms/<family>/port`, the family
template since **PLAT-23**; the couplings are family code now, but hand-written instead of generated):

| # | Area | Today | Target | Task |
|---|---|---|---|---|
| 1 | Build selection | `CMakeLists.txt`, `CMakePresets.json`: one board, one `ecu/rte.yaml`, NXP GCC path | project selection, generic toolchain | ARCH-04 |
| 2 | Feature switches | `ecu/platforms/evalboard_s32k144/Options.cmake` | `project.yaml` (features) | ARCH-04/05 |
| 3 | Integration | `…/main/src/main.cpp`, `systems/{CanSystem,SbcSystem,Kl30bSystem}`, `lifecycle/StaticBsp`, `BspSystem`, `os/isr/*`: SBC, KL30B, CAN0 ISR built in, one FlexCAN | generated integration, `early_init` role, `libs/bsw/canSystem` with the `can_phy` role and N buses, `libs/bsw/highSideSwitch` | ARCH-05/07 |
| 4 | Clock, timing, SBC values | `…/bsp/clock/boardClock.h` (40/80 MHz fixed), `bsp/can/canBitTiming.h`, `bsp/sbc/sbcConfiguration.h` by hand | from crystal (board) and system clock (project); computed bit timing; board (connection, MTPNV) plus project (modes) | ARCH-05/06/07 |
| 5 | MCU drivers in the board configuration | `bsp/io/outputPwm/ComplementaryPwm.h` (FTM), `bsp/adc/AnalogInput.*` (OpenBSW example, 12 bit), `mcu/mcu.h` (S32K148 with `#undef`), UART/`stdIo`/`SystemTimer` instances | `platforms/s32k1xx/bsp` with contract `OutputPwm`, `libs/bsp/bspAdc`, selection per derivative from `mcudb`, family template plus generation | ARCH-03/05 |
| 6 | Tasks | `freeRtosCoreConfiguration`, `ecu/asyncCoreConfiguration/Config.h`, tasks in `app.cpp`: fixed BSP/CAN/SWC | tasks from the project | RTE-02, ARCH-05 |
| 7 | Start-up, memory, safety | `main/linkerscript`, `startUp.S`, `bootHeader.S` (memory fixed); `safety/*` (MPU, `SafeIo` with LED pins, ADC reference check) | family template plus memory layout; family plus project (safety outputs) | ARCH-08, ARCH-05, SYS-06 |
| 8 | Bootloader | `bootloader/include/bl_config.h` (layout generated; diagnostic IDs still fixed), `src/target/*`, `updater/*` (FCF 0x400, 0x8000 fixed) | derivative plus project; `platforms/s32k1xx` plus `devices/uja113x` plus generated; family HAL plus memory layout | ARCH-08, COM-11 |
| 9 | `tools/canflash` | reads the generated layout (ARCH-08); identifier from the project (COM-11), default `nextbsw-bl`, former `S32K14x-BL` accepted (PLAT-08); status frame ID of the updater still fixed | reads generated project values | COM-11, PLAT-08 |
| 10 | Generators | `tools/bswgen` only S32K144, `FTM_INSTANCES = 4`, pin table LQFP100; `tools/rtegen` only `CAN_0`, one task; `ecu/configuration/busid/BusId.h` `CAN_0`; `ecu/uds` times coupled to `bl_config.h` | `mcudb` plus family output; buses and tasks from the project; generated | ARCH-03/04/07/08, RTE-02 |
| 11 | ECU = repository | `ecu/rte.yaml`, `ecu/rte/EvalBoard.dbc`; `boards/evalboard_s32k144/board.yaml` mixes hardware and usage (`exclusive_groups`) | `projects/evalboard_*`; board plus projects | ARCH-02/04, IO-04 |
| 12 | Tests, console, scripts | `test_bswgen.py`, `test_rtegen.py`, build paths `build/evalboard-s32k144` (eval board profile only); console `sbc failstart`, `kl30b on` in the normal image; J-Link scripts `device S32K144`; `CLAUDE.md` eval board rules global | synthetic profiles, build folder variable; expert functions switchable per project; from derivative/board; `boards/evalboard_s32k144/README.md` | ARCH-02/04/05, PLAT-07, TOOL-12 |

Stays as it is: `libs/bsw/sbcUja113x` (depends only on `ISpiDevice`, board values only as test data), `libs/bsp/bspSpi`,
bootloader core, `dbcgen`, `swc_build.m`.

## 10. Decisions for acceptance

| # | Question | Proposal |
|---|---|---|
| D1 | Exchange of the MCU drivers | static per family (like OpenBSW) + contract tests; virtual interfaces only from L2 upwards |
| D2 | Integration | generated from module manifests and configuration (section 5) |
| D3 | Role model | roles (`can_phy`, `power_supply`, `watchdog_trigger`, `early_init`, …) as the coupling between services and parts |
| D4 | Configuration | three levels in YAML (ruamel.yaml), schema with pydantic, shared loader `tools/nxconfig` |
| D5 | Derivative database | own `mcudb` from open sources (Zephyr hal_nxp/hal_stm32, CMSIS-SVD), only the fields needed |
| D6 | Bootloader | C without OpenBSW, HAL per family, same configuration source |
| D7 | Repository layout | section 8 (including `projects/`, `mcudb/`); **amended 2026-09-17:** `ecu/platforms/` stays and carries both port levels – `ecu/platforms/<family>` (family template, OpenBSW location) and `ecu/platforms/<board>` (feature switches). The original version "`ecu/` without `platforms/`" no longer applies |
| D8 | Language level | C++17 as today (GCC 11, OpenBSW-compatible); contracts via `static_assert`/contract test |
| D9 | OpenBSW | submodule unchanged; offer neutral contracts (ADC, SPI, watchdog) upstream later (PLAT-03) |
| D10 | Order | ARCH-02 → ARCH-03/04 → ARCH-05/06 → ARCH-07/08 → ARCH-09; the eval board runs after every step (regression test on the board) |
| D11 | Core profiles (target picture section 1) | **ARM Cortex-M and Cortex-R, 32 bit, little endian**; big endian (TMS570) explicitly not; the code distinguishes cores through the generated architecture, not through `__ARM_ARCH` |
| D12 | Bootloader ↔ application without VTOR (Cortex-R) | bootloader vectors forward Undefined, SVC, Prefetch Abort and Data Abort through a **fixed forwarding table at the application entry** (convention versioned); IRQ/FIQ through the VIM vector registers (section 7) |
| D13 | Family name of the TI Cortex-R derivatives | `rm4x` (TI Hercules RM4x, little endian) |
| D14 | Sources for rm4x: derivative headers, flash library, FreeRTOS port | **open until the licence and source review PLAT-44**; candidates according to ARCH-19: HALCoGen register headers, TI F021 flash API (possibly as a workstation dependency), FreeRTOS `GCC/ARM_CRx_No_GIC`. None of these sources is used or checked in before the review result is released |
| D15 | Platform rule (section 6, P1–P6) | **Every family in the OpenBSW layout and self-contained; `platforms/common` only for parts without an OpenBSW counterpart, exceptions only with an entry in P4; family port at application level `ecu/platforms/<family>`; `rm4x` OpenBSW-conformant with the acceptance criterion "referenceApp/unitTest builds for RM46"** |

Acceptance: D1–D10 on 2026-09-15; D11–D13 as proposed and D14 (procedure only; which sources are used is decided after
PLAT-44) on 2026-09-17 (audit ARCH-19); D15 and the amendment of D7 on 2026-09-17 (decision board, ARCH-30 F1/F3, RM46
option A, **ARCH-31**). Implementation: PLAT-54 (move of the family port), PLAT-55 (startup in one place), ARCH-27 (hard
fault handler per family, `libs/bsw/faultDump`), PLAT-35 (CRx port under `platforms/rm4x`), PLAT-53 (CMSIS header
upstream). Until these are done the repository deviates from the rule at the places named in P4 and P5; the rule applies
to every new placement from now on. The rejected alternatives are in the history of this file.

## 11. Review questions for reviews and agent tasks

Every change to reusable code, generators or the bootloader must answer these questions with "yes, without a code
change" or justify the exception:

1. Does it work on a board **without an SBC** (different or passive CAN transceiver, MCU's own watchdog)?
2. Does it work with a **different crystal** and a different system clock?
3. Does it work with a **different derivative** of the same family (flash, RAM, instances, package) – and is the way
   to **S32K3/STM32** and to **Cortex-R (rm4x)** not blocked?
4. Does it work with **two or more CAN buses** and different IDs?
5. Does it work with **several tasks** and freely chosen priorities?
6. Is every new value in **exactly one configuration level** (section 4), and does the generator derive what is
   derived?
7. Is there a **host test with its own test profile**, not just against the eval board?
8. Does the **core logic of the tools stay free of Qt** (Studio requirement), and does validation deliver messages
   with a YAML path?
9. For device drivers: is the granularity chosen according to [`treiber-granularitaet.md`](treiber-granularitaet.md)
   (generic behaviour or hardware-near per protocol), and are the project parameters in physical units of the
   function?
10. For new files under `platforms/`: does the placement follow the **platform rule** (section 6) – does platform code
   live per family in the OpenBSW layout, can `platforms/<family>` be built with `libs/` alone, and does
   `platforms/common` contain only parts that have no counterpart in OpenBSW (otherwise an entry in P4)?
