"""Checks across entries and levels (doc/architecture/README.md, section 4 "Validierung").

- derivative: core and FPU variant known (toolchain), patterns, instance data, memory regions, pin multiplexing and device header usable;
  clock entries related (instance clock domains and the flash clock are keys of clock.max_hz, the last wait state
  band covers its maximum, gpio ports have channels, names in the clock model inputs resolve, PLAT-70)
- board: names unique, instances and pins exist in the derivative, pin functions possible in the board's package
  (pin multiplexing), references resolve, PWM wiring consistent
- project: selections and parameters refer to board entries
- interrupt priorities: every used CAN bus has a rank (ARCH-33), every rank of the project fits the NVIC priority
  bits of the derivative and the RTOS syscall level (InterruptPriorities used by bswgen, platforms/common/interruptPriority); on a VIM core the ranks order the
  channels that are not fixed, without syscall level (VimChannelOrder, ARCH-23)
- resources: of everything the project uses, no pin, peripheral instance, ADC channel or timer channel is
  claimed twice; a timer instance serves either one PWM group or input captures (counter mode)
- modules (libs/bsw/<module>/module.yaml, libs/bsp, board port): manifests consistent, board devices assigned to a
  module (module or parts), connections wired to board entries of the right kind, parameters of the project against
  the module's parameter schema (per device, and per module for modules with project_parameters), module instances
  and the role can_phy of every used CAN bus (Integration, used by bswgen); one provider of the role power_supply
  (one SBC) and exactly the number of used SPI buses a module requires (spi_device) per device (ARCH-44)
- requirements of the family port (PLAT-95): the manifest platforms/<family>/port/port.yaml states what the
  hand-written port of the family needs but no schema asks for (internal ADC channels it reads by name, CAN buses
  it wires by hand, board values and project sections its code uses); checked against project, board and
  derivative for a project that is built with the port (its board has a board port ecu/platforms/<board>)
- serialized ADC access (adc.py): conversion time and polling bound derived from the converter settings and the
  clock model, checked against the interrupt suspension the project accepts (AdcAccessPlan used by bswgen)
- memory (memory.py): partitioning of code flash and RAM for bootloader and application, bootloader bus and device
  hooks (MemoryLayout, BootloaderPlan used by blgen and canflash)
- non-volatile memory (nvm.py, NVM-07): partition of the emulated EEPROM against the codes of the derivative's
  partition command (size, split, the one code of a split with several), fixed block places and write policies
  (NvmPlan used by bswgen, layout_blocks for rtegen)
- diagnostic addresses (diag.py): UDS CAN ids (11 bit, no collision with the project's DBC), ISO-TP parameters,
  bootloader identification and the flash area the application searches (DiagPlan used by blgen and canflash)
- diagnostic description (diagdesc.py, ARCH-51): data identifiers, routines, sessions, security levels and DTCs of
  `diag.yaml` - unique identifiers inside the ranges of ISO 14229-1, lengths, and references into rte.yaml and into
  the file itself (DiagDescriptionPlan for COM-28, NVM-03 and TOOL-43)
- PWM counter (pwm_timing, ARCH-33): frequency and dead time of every used PWM group representable by the counter of
  the family's PWM timer at the clock of the clock model (PwmTiming used by bswgen)
- power stages (powerstage.py): minimum dead time of the used PWM groups from the facts of their power stage,
  short-circuit current and blanking (PowerStagePlan)
- sensor characteristics (sensor.py): integer conversion of the used ADC channels to signed physical values (board
  sensor, project load), int32/int64 ranges and table points (SensorPlan used by bswgen and rtegen, IO-18)
- sensed module levels (sensed.py, IO-21): parameters with `sense:` (e.g. the load current levels of a high-side
  switch in mA) against the characteristic of the wired ADC channel - unit present (board facts), level in range
- clocks (clock.py): oscillator and system clock against the derivative, PLL, dividers, peripheral clocks, CAN bit
  timing and UART dividers of the used buses (family clock model, ClockPlan used by bswgen)
- CAN task order (COM-23): the CAN task of the RTE (rte.yaml can.task) runs below the task of the CAN system, a
  warning at its priority otherwise (latency promise of the transmit gate and the bus state)
- unconfirmed derivative values (ARCH-37): a warning for every derivative entry marked verify: true whose values the
  project uses (read while the project is checked, or used as a whole: switched-on instances, pins, MPU)
- name lengths (ARCH-43): names of used CAN buses and controllers and the log labels of module instances against the
  longest name the code takes, read from the constant in the C++ code (NAME_LIMITS; static_asserts of SYS-16)
- BSW task stacks (bswtasks.py, PLAT-26): project.yaml bsw_tasks against ecu/configuration/taskStacks.yaml (floor,
  granularity, allowed merges), sum of all task stacks against the application RAM (BswTaskPlan used by rtegen)
- watchdog timing (SYS-23, finding F2 of doc/safety/concept.md): three services of the MCU watchdog by the safety
  task fit into its time-out, values read from the constants in the C++ code (WATCHDOG_TIMING), message at the
  safety task of the BSW task manifest
"""

import re
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from fractions import Fraction
from math import ceil, floor
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from . import adc, calibration, clock, diag, diagdesc, features, memory, nvm, powerstage, sensed, sensor
from . import i2c as nxi2c
from . import pinevent  # IO-51
from . import degraded  # SYS-117
from . import security  # NVM-14
from . import hwthreshold  # SYS-86
from . import samplegroup  # IO-62
from . import safestate  # SYS-116
from . import pwmgate  # SYS-95
from .can import c_name
from .config import CORES, REPO, RTE_LEVEL, Config, ModuleSet, Reporter, Source, _plain, _read, line_of
from .messages import Message
from .messages import format_path as _fmt
from .model import (CAN_WAKEUP_FRAME, POWER_STAGE_UNITS, VERIFY_KEY, CanWakeupFrame, Derivative, DerivativeUse,
                    Manifest, ParameterSpec, PinmuxTable, fact_paths)
from . import supply  # IO-33
from .model import WAKE_INPUT_USE  # IO-33
from .model import RTE_RESERVED_PREFIXES  # RTE-18
from .model import DISTRIBUTION_CLASSES, LICENSE_REF_CLASSES, PROVENANCE_ACCESS, spdx_identifiers  # PLAT-49
from . import bswtasks  # PLAT-26
from . import console as nxconsole  # PLAT-27

BUS_KINDS = ("spi", "can", "uart", "i2c")
SELECTION_KINDS = ("spi", "can", "uart", "i2c", "adc", "input_capture", "dio")
BUS_PINS = ("sck", "sin", "sout", "pcs", "rx", "tx", "sda", "scl")
GPIO = "GPIO"


def run(cfg: Config, rep: Reporter, port_requirements_check: bool = True):
    """port_requirements_check=False: a caller that checks an incomplete project on the way to a complete
    one (the wizard of nxconfig new probes clock candidates with a project.yaml that has nothing but the
    clock). Its sections do not exist yet, so the requirements of the family port would report what the
    caller is about to write - and would hide the finding the caller is actually looking for.

    TOOL-85: the checks that read *folders* of a neXtBSW tree take `cfg.running_tree` - the tree the caller said
    it runs as. Two of them deliberately do not, and still read the tree this package lies in: `name_lengths`
    (ARCH-43, limits from libs/bsw/<module>/include) and `safety_timing` (SYS-25, constants from the family port
    and libs/bsw). They read **C++ source of the tree**, and a caller may hand a tree that carries only
    configuration - the synthetic profile trees of the Studio tests do exactly that, and a limit that cannot be
    read is an error, not a warning. Lifting this needs the fixtures to be complete trees and belongs with the
    Studio's own switch to the parameter (TOOL-73); until then it is the remaining part of the TOOL-61 limit.
    """
    patterns = _derivative(cfg, rep)
    _board(cfg, rep, patterns)
    # ARCH-37: from here on everything is about what the project uses; the derivative values read on the way are
    # recorded (model.DerivativeUse), the checks of the derivative and the board above are not.
    # Ignored readers check the derivative or the board, not the project: clock._limits the derivative's own limits
    # (min <= max), sensor._Channel the characteristic of every board ADC channel (sensor.check keeps the plans of
    # the used ones; their instances are recorded as used in _used_entries).
    use = DerivativeUse(ignore=(clock._limits, sensor._Channel.__init__))
    claims: dict[str, Claim] = {}
    with use:
        with use.phase("the project checks (internal ADC channels, CAN message buffers, interrupt ranks)"):
            _project(cfg, rep, patterns)
        if not rep.has_errors:
            claims = _resources(cfg, rep)
        with use.phase("the module integration"):
            cfg.integration = integrate(cfg, rep, cfg.modules)
        _can_task_order(cfg, rep)                   # COM-23: RTE's CAN task below the CAN system's task
        _safety_roles(cfg, rep)                     # TOOL-110: the pins of the safety section fit their role
        # IO-51: events on digital inputs against the pin interrupt facts of the derivative (after the module
        # integration: Hall signals share the vectors); no gate, its findings name only pin_events entries
        with use.phase("the pin events (pinevent.py, IO-51)"):
            cfg.pin_events = pinevent.check(cfg, rep)
        name_lengths(cfg, rep)                      # ARCH-43: names against the limits of the code
        supply.check(cfg, rep)                      # IO-33: battery monitor of the SBC, local wake-up inputs
        cfg.safety_timing = safety_timing(cfg, rep)  # SYS-25/SYS-23: safety task times, watchdog service derived
        degraded.check(cfg, rep)                    # SYS-117: safe-output groups and degraded mappings
        cfg.safe_states = safestate.check(cfg, rep)  # SYS-116: freewheel and active short per PWM group
        pwmgate.check(cfg, rep)                     # SYS-95 (DFA-4 a): PWM group without a bound safe_off output
        if not rep.has_errors:
            with use.phase("the memory layout of bootloader and application (memory.py)"):
                cfg.memory, cfg.bootloader = memory.check(cfg, rep)
            # SYS-89: production profile without XCP, refuse with a safety relevant calibration parameter
            calibration.check(cfg, rep)
        # PLAT-26: stacks and merges of the BSW tasks (project.yaml bsw_tasks); after the memory layout, whose RAM
        # the sum of all task stacks is checked against. No gate: its findings name only bsw_tasks entries.
        cfg.bsw_tasks = bswtasks.check(cfg, rep)
        # PLAT-27: console and logger of the application (project.yaml console); after the memory layout, whose
        # application RAM the logger ring buffer is checked against. No gate: its findings name only console entries.
        nxconsole.check(cfg, rep)
        if not rep.has_errors:
            with use.phase("the NvM partition and block places (nvm.py)"):
                cfg.nvm = nvm.check(cfg, rep)
        # NVM-14: keys of the security module. No gate: WRITE_PROTECTION is refused whatever else is wrong.
        with use.phase("the keys of the security module (security.py)"):
            security.check(cfg, rep)
        if not rep.has_errors:
            with use.phase("the diagnostics and the bootloader security access (diag.py)"):
                cfg.diagnostics = diag.check(cfg, rep)
        # ARCH-51: the diagnostic description reads no derivative value, so it stands outside the phases; it
        # runs even after an error above, because its findings are independent of board and derivative.
        cfg.diag_description = diagdesc.check(cfg, rep)
        if not rep.has_errors:
            with use.phase("the clock plan (clock.py: oscillator, PLL, bus dividers, peripheral clocks)"):
                cfg.clock = clock.check(cfg, rep)
        if not rep.has_errors:
            with use.phase("the ADC access bound (adc.py)"):
                cfg.adc_access = adc.check(cfg, rep)
        if not rep.has_errors:
            with use.phase("the PWM counter check"):
                _pwm_timing(cfg, rep)
        if not rep.has_errors:
            with use.phase("the ADC sampling over a PWM period (IO-31)"):
                _adc_sampling(cfg, rep)
        if not rep.has_errors:
            with use.phase("the power stage check (powerstage.py)"):
                cfg.power_stages = powerstage.check(cfg, rep)
        # SYS-86: hardware thresholds (comparator -> fault input of a PWM timer); needs the clock plan (fault filter)
        if not rep.has_errors:
            with use.phase("the hardware thresholds (hwthreshold.py, SYS-86)"):
                cfg.hw_thresholds = hwthreshold.check(cfg, rep)
        # IO-62: sample groups (PWM trigger -> router -> sequencer -> ADC); after the hardware thresholds, whose fault
        # routes take router outputs, and after the ADC access bound (conversion time) and the clock plan
        if not rep.has_errors:
            with use.phase("the sample groups (samplegroup.py, IO-62)"):
                cfg.sample_groups = samplegroup.check(cfg, rep)
        if not rep.has_errors:
            with use.phase("the sensor characteristics (sensor.py)"):
                cfg.adc_sensors = sensor.check(cfg, rep)
        if not rep.has_errors:
            with use.phase("the module levels of analog connections (sensed.py)"):
                cfg.sensed_levels = sensed.check(cfg, rep)
        # IO-28: rate, time limit and FlexIO resources of the used I2C buses (needs the clock plan)
        if not rep.has_errors:
            with use.phase("the I2C buses (i2c.py: rate, time limit, FlexIO shifters and timers)"):
                cfg.i2c_buses = nxi2c.check(cfg, rep)
        # PLAT-95: what the family port needs from project, board and derivative. Last, and never a gate: its
        # findings are independent of everything above, and an unmet requirement of the port must not swallow the
        # finding a check further up would have reported (they name different values of different levels).
        if port_requirements_check:
            with use.phase("the requirements of the family port (platforms/<family>/port/port.yaml)"):
                port_requirements(cfg, rep, cfg.running_tree)
        # TOOL-120: the drivers of the family port, here I2C. No gate and not tied to port_requirements_check: a
        # missing driver is no missing section of an incomplete project.
        with use.phase("the drivers of the family port (I2C)"):
            _i2c_drivers(cfg, rep, cfg.running_tree)
        # TOOL-114: a project that is built needs an ECU description. Tied to port_requirements_check like the
        # requirements of the port: the probes of the wizard check a project.yaml that has nothing but the clock.
        if port_requirements_check:
            rte_required(cfg, rep, cfg.running_tree)
        # ARCH-58: the feature switches of the build, derived from project, board and the port statement of the
        # family (ecu/platforms/<family>/features.yaml). Outside the phases and no gate, like PLAT-95 above: it
        # reads no derivative value and its findings are independent of everything before it.
        cfg.features = features.derive(cfg, rep, cfg.running_tree)
    _used_entries(cfg, use, claims)                     # outside `with use`: its own reads are no use
    _unconfirmed(cfg, rep, use)


# ----------------------------------------------------------------------------------------------- safety roles
# TOOL-110 (finding F6): what the three pins of `safety` do at run time on the s32k1xx family port, and what
# follows for the choice. Code: platforms/s32k1xx/port/safety/safeIo (SafeState.cpp, SafeIo.cpp), tools/bswgen
# (collect(): SAFETY_TEST as GPIO input with PULLDOWN; collect_safe_io(): locked_pin in the lock list).
# - safe_state_output is the INDICATOR of the safe state: enterSafeState() sets its pin to PINDISABLE (the pin
#   floats), leaveSafeState() sets it to GPIO | STRENGTH_ON - with the output latch as the application left it.
#   Clear errors: it must not be the locked pin, nor a safe_off output of a module - bswgen refuses both
#   (collect_safe_io: "is switched by the safe state ... but it is locked"), so nxconfig says it first. Nor any
#   other output a device driver owns (a digital_output connection of a module, e.g. the diagnosis enable of a
#   high-side switch): the BSW safety layer and a driver would both own the pin - the safety layer disables and
#   re-muxes it behind the driver's back (BSW mechanisms and driver mechanisms stay separate, CLAUDE.md).
#   SYS-87 (user decision 2026-09-25, variant A): an output the APPLICATION writes (rte.yaml dio.out.<name>) is a
#   WARNING. Leaving the safe state brings back the level the application wrote last, so an actuator there would
#   float in the safe state and switch on again without an enable of its driver (what SSR-004 rules out for power
#   outputs; assumption of use AoU-30 of doc/safety/concept.md). nxconfig cannot tell an LED from the enable of a
#   pump, so it does not refuse: `safety.safe_state_output_shared_with_application: "<reason>"` confirms the case on
#   purpose and silences the warning; the key without the case is superfluous and warned about itself.
# - test_pin is the input the register check watches (dir == input) and the console command `safety dst`/`est`
#   disables and restores. The schema calls it an "unconnected pin"; a pin the board file describes as wired to a
#   signal - used by the project or not - is not unconnected, and its pull-down would load that net. An error.
# - locked_pin: the PCR lock freezes only the pin configuration; the level (PDOR/PSOR/PCOR) stays writable, so an
#   application that writes the output is no conflict. What conflicts is code that RECONFIGURES the pin (direction,
#   function) - SafeIo sees it as a register fault. No configuration value says whether a driver does that, so it is
#   not checked here (reported with TOOL-110 instead of guessed).


def board_pin_users(cfg: Config) -> dict[str, list[str]]:
    """Every pin the board file names, with what it is wired to ('dio.outputs PUMP_EN', 'can CAN0 rx', ...)."""
    source = cfg.sources.get("board")
    tree = _plain(source.tree) if source is not None else {}
    users: dict[str, list[str]] = {}

    def walk(node, where: list[str]):
        if isinstance(node, dict):
            name = node.get("name") if isinstance(node.get("name"), str) else None
            here = where + [name] if name else where
            for key, value in node.items():
                if key == "pin" and isinstance(value, str):
                    users.setdefault(value, []).append(" ".join(here) or "board")
                elif isinstance(value, (dict, list)):
                    walk(value, here + [str(key)])
        elif isinstance(node, list):
            for item in node:
                walk(item, where)

    for key, value in (tree.items() if isinstance(tree, dict) else ()):
        if key not in ("schema_version", "board"):
            walk(value, [str(key)])
    return users


def _driver_outputs(cfg: Config, output: str) -> list[tuple[str, bool]]:
    """The module connections wired to the digital output `output`: (what, safe_off) for each."""
    found = []
    integration = cfg.integration
    if integration is None or cfg.modules is None:
        return found
    for dev in integration.devices:
        manifest = cfg.modules.manifests.get(dev.module)
        for key, spec in (manifest.connect.items() if manifest is not None else ()):
            if spec.kind == "digital_output" and dev.connect.get(key) == output:
                found.append((f"connect.{key} of device {dev.name} (module {dev.module})", bool(spec.safe_off)))
    return found


def application_writers(cfg: Config, output: str) -> list[str]:
    """SYS-87: the ports of the ECU description that write the digital output `output` (rte.yaml connections with
    the target dio.out.<output>), as 'Component.Port.Element'."""
    target = f"dio.out.{output}"
    ecu = cfg.rte
    if ecu is None:
        return []
    return [port for port, targets in ecu.connections.items()
            if target in ([targets] if isinstance(targets, str) else targets)]


def _indicator_writers(cfg: Config, rep: Reporter, indicator: str, confirmed: str | None):
    """SYS-87: warning at safety.safe_state_output if the application writes the indicator and nothing confirms it;
    warning at the confirmation if the application does not write it (section comment above)."""
    key = "safe_state_output_shared_with_application"
    if cfg.rte is None and cfg.project.project.rte is not None:
        return                                  # the ECU description could not be read: reported where it is
    writers = application_writers(cfg, indicator)
    if writers and confirmed is None:
        rep.warning("project", ("safety", "safe_state_output"),
                    f"{indicator} is the indicator of the safe state and the application writes it (rte.yaml "
                    f"{', '.join(writers)} -> dio.out.{indicator}): entering the safe state disables the pin, so it "
                    "floats, and leaving it switches the pin back with the level the application wrote last. An "
                    "actuator there would float in the safe state and come back on without an enable (AoU-30 of "
                    "the safety concept). Choose an output the application does not write, or - if it is only a "
                    f"display such as an LED - confirm it with safety.{key}: \"<reason>\"")
    elif confirmed is not None and not writers:
        rep.warning("project", ("safety", key),
                    f"superfluous: the application does not write {indicator} (no rte.yaml connection to "
                    f"dio.out.{indicator}), so there is nothing to confirm. Remove the key - a confirmation that "
                    "stays would silence the warning once a port is connected to the indicator later")


def _safety_roles(cfg: Config, rep: Reporter):
    """TOOL-110: the pins of `safety` against their role (section comment above); messages at safety.<key>."""
    safety = cfg.project.safety
    if safety is None:
        return
    indicator, locked, test = safety.safe_state_output, safety.locked_pin, safety.test_pin
    if indicator == locked:
        rep.error("project", ("safety", "locked_pin"),
                  f"safe_state_output and locked_pin are both {locked}: the safe state switches the indicator's pin "
                  "(its pin control register is written), which the lock of locked_pin refuses - bswgen stops. "
                  "Choose two different outputs")
    for what, safe_off in _driver_outputs(cfg, indicator):
        if safe_off:
            rep.error("project", ("safety", "safe_state_output"),
                      f"{indicator} is the safe_off output {what}: the safe outputs table drives it off and locks "
                      "its pin, the indicator of the safe state would disable and release it - bswgen stops. "
                      "Choose an output of its own as indicator")
        else:
            rep.error("project", ("safety", "safe_state_output"),
                      f"{indicator} is the output {what}: a device driver owns it, and the indicator of the safe "
                      "state would disable the pin and switch it back to GPIO behind the driver (with the level "
                      "last written) - BSW safety mechanisms and driver mechanisms stay separate. Choose an output "
                      "of its own as indicator")
    _indicator_writers(cfg, rep, indicator, safety.safe_state_output_shared_with_application)
    users = board_pin_users(cfg).get(test)
    if users:
        rep.error("project", ("safety", "test_pin"),
                  f"test_pin {test} is wired on board {cfg.board.board.name} ({'; '.join(users)}), but it has to be "
                  "an unconnected pin: it is configured as a GPIO input with pull-down and checked every safety "
                  "cycle, and the console command `safety dst`/`est` switches it. Choose a pin the board does not "
                  "use (nxconfig new --safety-candidates --board <board> lists them) and check the schematic")


# ----------------------------------------------------------------------------------------------- CAN task order
# COM-22/COM-23 (review question 5 of COM-22): the task of the CAN system (module canSystem, lifecycle.task, `can`)
# evaluates the transmit gate (readiness of the transceivers) and the bus state of every CAN bus; the RTE takes both
# into the signal qualifiers and the transmit decision of its CAN task (rte.yaml can.task). "QLF_ERROR within one
# cycle of that task, and no frame after the transceiver reported not ready" holds only while the RTE's CAN task runs
# below the CAN system's task: otherwise it runs first in the tick in which the CAN system's 10 ms cycle picks up a
# change (bus state; gate of a transceiver without readiness reports) and sees it one cycle later (host model
# libs/bsw/canSystem/test/host/CanPhyGateHostTest.cpp). The priorities are ordering keys of the BSW task manifest
# (ecu/asyncCoreConfiguration/tasks.yaml, tools/rtegen) and of rte.yaml tasks[].priority.

TASKS_MANIFEST_NAMES = ("ecu", "asyncCoreConfiguration", "tasks.yaml")
CAN_SYSTEM_MODULE = "canSystem"


def tasks_manifest(tree: Path | None = None) -> Path:
    """The BSW task manifest of a tree (TOOL-85: of the tree the tools run as, not of a fixed one)."""
    return (REPO if tree is None else Path(tree)).joinpath(*TASKS_MANIFEST_NAMES)


@dataclass(frozen=True)
class TaskKeys:
    """Ordering keys of the BSW task manifest: BSW tasks by name and the default application task."""

    bsw: dict[str, int]
    default: tuple[str, int] | None                 # application_default (name, priority), None if not readable


def bsw_task_keys(manifest: Path | None = None) -> TaskKeys:
    """Priorities of the BSW task manifest; empty if it cannot be read (tools/rtegen reports a broken manifest)."""
    manifest = tasks_manifest() if manifest is None else manifest
    try:
        data = YAML(typ="safe").load(Path(manifest).read_text(encoding="utf-8"))
    except (OSError, YAMLError):
        return TaskKeys({}, None)
    if not isinstance(data, dict):
        return TaskKeys({}, None)
    bsw = {t["name"]: t["priority"] for t in data.get("tasks") or []
           if isinstance(t, dict) and isinstance(t.get("name"), str) and isinstance(t.get("priority"), int)}
    default = data.get("application_default")
    if isinstance(default, dict) and isinstance(default.get("name"), str) and isinstance(default.get("priority"), int):
        return TaskKeys(bsw, (default["name"], default["priority"]))
    return TaskKeys(bsw, None)


def can_task_order_problem(rte_task: str, rte_priority: int, system_task: str, system_priority: int,
                           period_ms: int | None) -> str | None:
    """Why the RTE's CAN task breaks the latency promise of the CAN system, None if it runs below the CAN system."""
    if rte_priority < system_priority:
        return None
    cycle = f"{period_ms} ms" if period_ms else "one cycle"
    later = f"up to {2 * period_ms} ms instead of {period_ms} ms" if period_ms else "one cycle later"
    return (f"CAN task {rte_task} of the RTE (priority {rte_priority}) does not run below task {system_task} of the "
            f"CAN system (priority {system_priority}): bus off and a transceiver that is not ready reach its signal "
            f"qualifiers {later} (promise: within {cycle}), and a frame can still be written after the transceiver "
            f"went not ready; give it a priority below {system_priority} (COM-22, COM-23)")


def _can_task_order(cfg: Config, rep: Reporter, manifest: Path | None = None):
    """Warning with YAML path (rte.yaml) if the RTE's CAN task does not run below the task of the CAN system."""
    ecu = cfg.rte
    if ecu is None or ecu.can is None:
        return
    system = cfg.modules.manifests.get(CAN_SYSTEM_MODULE)
    if system is None or system.lifecycle is None:
        return
    keys = bsw_task_keys(tasks_manifest(cfg.running_tree) if manifest is None else manifest)
    system_priority = keys.bsw.get(system.lifecycle.task)
    if system_priority is None:
        return
    if ecu.tasks:
        names = [t.name for t in ecu.tasks]
        name = ecu.can.task if ecu.can.task is not None else (names[0] if len(names) == 1 else None)
        if name not in names:
            return                                  # unknown or ambiguous can.task: tools/rtegen reports it
        index = names.index(name)
        task = ecu.tasks[index]
        problem = can_task_order_problem(name, task.priority, system.lifecycle.task, system_priority, task.period_ms)
        path = ("tasks", index, "priority")
    else:
        if keys.default is None:
            return
        name, priority = keys.default               # one application task with the manifest's default key
        problem = can_task_order_problem(name, priority, system.lifecycle.task, system_priority, ecu.base_period_ms)
        path = ("can", "task") if ecu.can.task is not None else ("can",)
    if problem is not None:
        rep.warning(RTE_LEVEL, path, problem)


# ----------------------------------------------------------------------------------------------- unconfirmed values
# ARCH-37 (finding DOC-08): `verify: true` in mcudb marks a value without evidence. A project is warned about exactly
# the unconfirmed values it uses, one warning per entry with the values and what the project needs them for.

USE_LIST = 4                    # purposes named in one warning, the rest is counted


def _used_entries(cfg: Config, use: DerivativeUse, claims: dict[str, "Claim"]):
    """Derivative entries the project uses as a whole, not through reads of single values.

    - peripheral instances the project switches on (clock.used_instances: selected board entries, PWM groups,
      internal ADC channels, bootloader bus): their facts (channels, pattern, internal inputs) were checked when the
      board was, and the generators configure them,
    - the pin multiplexing of the board's package for every pin the project claims (the generators write the pin
      functions from it),
    - the MPU region count, which only the generators read (bswgen/stm32gen NX_MPU_REGIONS in every build).
    """
    d = cfg.derivative
    for name, (what, _) in clock.used_instances(cfg).items():
        inst = d.instances.get(name)
        if inst is not None:
            use.entry(inst, what)
    ref = d.pinmux.get(cfg.board.board.package)
    if ref is not None:
        for resource, claim in claims.items():
            if resource.startswith("pin "):
                use.entry(ref, f"{resource} ({claim.owner})", "signals" if ref.signals is not None else "file")
    if d.mpu is not None:
        use.entry(d.mpu, "the MPU region count of the generated board header (NX_MPU_REGIONS)", "regions")


def _unconfirmed(cfg: Config, rep: Reporter, use: DerivativeUse):
    """One warning per used derivative entry marked verify: true, in the order of the derivative file."""
    source = rep.sources.get("derivative")
    tree = source.tree if source is not None else None
    found = []
    for path, entry in fact_paths(cfg.derivative):
        entry_use = use.entries.get(id(entry))
        if entry_use is None or not entry.verify:
            continue
        values = f" ({', '.join(entry_use.fields)})" if entry_use.fields else ""
        purposes = list(entry_use.purposes)
        needed = ", ".join(purposes[:USE_LIST])
        if len(purposes) > USE_LIST:
            needed += f" and {len(purposes) - USE_LIST} more"
        text = (f"{_fmt(path)}{values} of {cfg.derivative.derivative.name} is marked verify: true (not confirmed by "
                f"a source), the project needs it for {needed}")
        found.append((line_of(tree, path + ("verify",)) or 0, path + ("verify",), text))
    for _, path, text in sorted(found, key=lambda f: f[0]):
        rep.warning("derivative", path, text)


# ----------------------------------------------------------------------------------------------- signal names
# Names of pin functions in the pin multiplexing tables (model.SignalTable), shared with the generators.


def bus_signal(cfg: Config, kind: str, bus, role: str) -> str:
    """Pin function of a bus pin: LPSPI1_SCK, LPSPI1_PCS0, FlexCAN0_RX, USART2_TX, I2C1_SDA, FlexIO_D3."""
    inst = cfg.derivative.instances.get(bus.instance)
    if role == "pcs":
        return f"{bus.instance}_PCS{bus.pcs.index}"
    if kind == "i2c" and inst is not None and inst.class_ == "flexio":
        return f"{bus.instance}_D{getattr(bus, role).flexio_pin}"
    return f"{bus.instance}_{role.upper()}"


def _flexio_pins(rep: Reporter, base: tuple, bus, class_: str) -> bool:
    """TOOL-120: `flexio_pin` of an I2C bus is needed exactly on a FlexIO instance (bus_signal reads it there).

    The schema cannot tell, because the class of the instance lies in the derivative: on a FlexIO a missing data
    pin is an error at its YAML path (False: the pin functions cannot be named), on an I2C instance a given one is
    never read - a warning, so nobody believes it does something.
    """
    ok = True
    for role in ("sda", "scl"):
        pin = getattr(bus, role)
        if class_ == "flexio" and pin.flexio_pin is None:
            rep.error("board", base + (role, "flexio_pin"),
                      f"{bus.instance} is a FlexIO instance: I2C bus {bus.name} needs the FlexIO data pin D<n> its "
                      f"{role.upper()} pin {pin.pin} is connected to (flexio_pin; pin function "
                      f"{bus.instance}_D<n>)")
            ok = False
        elif class_ != "flexio" and pin.flexio_pin is not None:
            rep.warning("board", base + (role, "flexio_pin"),
                        f"{bus.instance} is an I2C instance, not a FlexIO: flexio_pin is not read (the pin function "
                        f"is {bus.instance}_{role.upper()}) - remove it")
    return ok


def timer_signal(instance: str, channel: int) -> str:
    return f"{instance}_CH{channel}"


def adc_signal(instance: str, channel: str) -> str:
    return f"{instance}_{channel}"


# ----------------------------------------------------------------------------------------------- derivative


@dataclass
class Pinmux:
    """Pin multiplexing of the board's package."""

    package: str
    signals: dict[str, dict[str, int | dict[str, int]]]
    complete: bool
    pins: set[str]

    def selector(self, signal: str, pin: str) -> int | dict[str, int] | None:
        return self.signals.get(signal, {}).get(pin)

    def functions(self, pin: str) -> list[str]:
        return sorted(s for s, pins in self.signals.items() if pin in pins)


@dataclass
class Patterns:
    pin: re.Pattern | None
    adc_channel: dict[str, re.Pattern]
    pinmux: Pinmux | None = None


def _regex(rep: Reporter, path, pattern: str) -> re.Pattern | None:
    try:
        return re.compile(pattern)
    except re.error as e:
        rep.error("derivative", path, f"invalid regular expression '{pattern}': {e}")
        return None


# Referenced mcudb files (pin multiplexing, device header derivative) change rarely but are read on every check,
# and round-trip YAML is slow (S32K148 table ~0.2 s): keep successful loads per file state.
_CACHE: dict[tuple, tuple] = {}


def _read_cached(rep: Reporter, level: str, file, model):
    stat = file.stat()
    key = (str(file.resolve()), model.__name__, stat.st_mtime_ns, stat.st_size)
    if key in _CACHE:
        tree, result = _CACHE[key]
        rep.sources[level] = Source(file, tree)
        return result
    result = _read(rep, level, file, model)
    if result is not None:
        _CACHE[key] = (rep.sources[level].tree, result)
    return result


def pinmux(cfg: Config, rep: Reporter | None = None) -> Pinmux | None:
    """Pin multiplexing of the board's package; None if the derivative has no data for it or it is unreadable."""
    rep = rep if rep is not None else Reporter()
    package = cfg.board.board.package
    ref = cfg.derivative.pinmux.get(package)
    if ref is None:
        return None
    if ref.signals is not None:
        signals = ref.signals
    else:
        file = cfg.derivative_file.parent / ref.file
        if not file.is_file():
            rep.error("derivative", ("pinmux", package, "file"), f"pin multiplexing file {ref.file} not found")
            return None
        table = _read_cached(rep, "pinmux", file, PinmuxTable)
        if table is None:
            return None
        signals = table.signals
    return Pinmux(package, signals, ref.complete, {pin for pins in signals.values() for pin in pins})


def described_derivative(cfg: Config, rep: Reporter | None = None) -> Derivative | None:
    """Derivative the device header was written for (the derivative itself or a superset in the same family)."""
    d = cfg.derivative
    if d.device_header is None:
        return None
    if d.device_header.describes == d.derivative.name:
        return d
    rep = rep if rep is not None else Reporter()
    file = cfg.derivative_file.parent / f"{d.device_header.describes}.yaml"
    if not file.is_file():
        rep.error("derivative", ("device_header", "describes"),
                  f"no derivative description {file.name} in {cfg.derivative_file.parent}")
        return None
    return _read_cached(rep, "device_header", file, Derivative)


def _memory(cfg: Config, rep: Reporter):
    names: set[str] = set()
    regions = []
    for i, r in enumerate(cfg.derivative.memory):
        where = ("memory", i)
        if r.name in names:
            rep.error("derivative", where + ("name",), f"duplicate memory region {r.name}")
        names.add(r.name)
        if r.sector_size is not None and r.sectors is not None:
            rep.error("derivative", where + ("sectors",), f"{r.name}: give either sector_size or sectors")
        elif r.sectors is not None:
            total = sum(g.size * g.count for g in r.sectors)
            if total != r.size:
                rep.error("derivative", where + ("sectors",),
                          f"sectors of {r.name} cover 0x{total:X} bytes, the region has 0x{r.size:X}")
        if r.kind in ("code_flash", "data_flash"):
            if r.sector_size is None and r.sectors is None:
                rep.error("derivative", where, f"{r.kind} region {r.name} needs sector_size")
            elif r.sector_size is not None and r.size % r.sector_size:
                rep.error("derivative", where + ("size",),
                          f"size 0x{r.size:X} of {r.name} is no multiple of sector_size 0x{r.sector_size:X}")
            if r.program_size is not None and any(size % r.program_size for _, size in r.sector_table()):
                rep.error("derivative", where + ("program_size",),
                          f"sectors of {r.name} are no multiple of program_size {r.program_size}")
        elif r.sectors is not None or r.program_size is not None:
            rep.error("derivative", where, f"sectors and program_size only apply to flash regions ({r.name} is {r.kind})")
        for other in regions:
            if r.start < other.start + other.size and other.start < r.start + r.size:
                rep.error("derivative", where, f"{r.name} overlaps {other.name}")
        regions.append(r)


def _mhz(hz) -> str:
    return f"{float(Fraction(hz) / 1_000_000):g} MHz"


def _clock_facts(cfg: Config, rep: Reporter):
    """PLAT-70: relations between the clock entries of the derivative, each finding with its YAML path.

    - the clock domain of an instance (instances.<x>.clock.domain, clock.pcc.<gate>.domain) is a key of clock.max_hz,
    - the flash wait states refer to a clock of clock.max_hz and their last band covers its maximum,
    - the names in the inputs of a family clock model (external_max_hz, oscillator_monitor, domain_dividers) refer to
      clocks the derivative describes, and the parents of the domains form no cycle.
    """
    d = cfg.derivative
    clk = d.clock
    limits = clk.max_hz if clk is not None else {}
    known = ", ".join(limits) or "none, the derivative has no clock.max_hz"
    entries = [(("instances", name, "clock"), inst.clock) for name, inst in d.instances.items()]
    entries += [(("clock", "pcc", gate), entry) for gate, entry in (clk.pcc.items() if clk is not None else ())]
    for where, entry in entries:
        if entry is not None and entry.domain is not None and entry.domain not in limits:
            rep.error("derivative", where + ("domain",), f"clock domain {entry.domain} is no key of clock.max_hz "
                                                         f"(available: {known})")
    if clk is None:
        return
    fws = clk.flash_wait_states
    if fws is not None:
        if fws.clock not in limits:
            rep.error("derivative", ("clock", "flash_wait_states", "clock"),
                      f"flash clock {fws.clock} is no key of clock.max_hz (available: {known})")
        elif fws.bands[-1].max_hz < limits[fws.clock]:
            last = len(fws.bands) - 1
            rep.error("derivative", ("clock", "flash_wait_states", "bands", last, "max_hz"),
                      f"the last band ends at {_mhz(fws.bands[-1].max_hz)}, below the maximum {fws.clock} of "
                      f"{d.derivative.name} ({_mhz(limits[fws.clock])}, clock.max_hz.{fws.clock}): no wait states "
                      f"known for the clocks above it")
    sources = set(clk.source_numbers.numbers) if clk.source_numbers is not None else set()
    if clk.external_max_hz is not None:
        for name in clk.external_max_hz.max_hz:
            if name not in sources:
                rep.error("derivative", ("clock", "external_max_hz", "max_hz", name),
                          f"{name} is no clock source of clock.source_numbers (available: "
                          f"{', '.join(sources) or 'none'})")
    monitor = clk.oscillator_monitor
    if monitor is not None:
        if clk.oscillator is None:
            rep.error("derivative", ("clock", "oscillator_monitor"), f"{d.derivative.name} has no oscillator "
                                                                     f"(clock.oscillator) to monitor")
        if monitor.reference not in clk.internal:
            rep.error("derivative", ("clock", "oscillator_monitor", "reference"),
                      f"{monitor.reference} is no internal clock of clock.internal (available: "
                      f"{', '.join(clk.internal) or 'none'})")
    domains = clk.domain_dividers
    names = set(limits) | set(domains) | set(clk.internal) | sources
    for dom, entry in domains.items():
        where = ("clock", "domain_dividers", dom)
        for key, value in (("parent", entry.parent), ("multiple_of", entry.multiple_of),
                           ("other_source", entry.other_source.max_of if entry.other_source else None)):
            if value is not None and value not in names:
                rep.error("derivative", where + ((key, "max_of") if key == "other_source" else (key,)),
                          f"{value} is no clock of {d.derivative.name} (clock.max_hz, domain_dividers, internal or "
                          f"source_numbers)")
        seen, parent = [dom], entry.parent
        while parent in domains and parent not in seen:
            seen.append(parent)
            parent = domains[parent].parent
        if parent == dom:                               # members of the cycle only, not domains that lead into it
            rep.error("derivative", where + ("parent",), f"the parents of {dom} form a cycle "
                                                         f"({' -> '.join(seen + [parent])})")


def _core(cfg: Config, rep: Reporter):
    """Core known to the core table (toolchain options) and FPU variant possible for it."""
    info = cfg.derivative.derivative
    core = CORES.get(info.core)
    if core is None:
        rep.error("derivative", ("derivative", "core"), f"unknown core {info.core} (known: {', '.join(CORES)})")
        return
    variants = ", ".join(core.fpus)
    if info.fpu is not None and info.fpu not in core.fpus:
        rep.error("derivative", ("derivative", "fpu"), f"core {info.core} has no FPU variant {info.fpu} "
                  f"(possible: {variants})")
    elif info.fpu is None and len(core.fpus) > 1:
        rep.error("derivative", ("derivative", "fpu"), f"core {info.core} needs fpu ({variants})")
    # ARCH-23: the interrupt facts of the derivative follow the controller of the core
    bits, vim = info.nvic_priority_bits, cfg.derivative.vim
    if core.interrupt_controller == "nvic":
        if vim is not None:
            rep.error("derivative", ("vim",), f"core {info.core} has an NVIC, not a VIM (use nvic_priority_bits)")
        low, high = NVIC_PRIORITY_BITS[info.core]
        if bits is not None and not low <= bits <= high:
            possible = str(low) if low == high else f"{low}..{high}"
            rep.error("derivative", ("derivative", "nvic_priority_bits"),
                      f"core {info.core} implements {possible} NVIC priority bits, not {bits}")
        return
    if bits is not None:
        rep.error("derivative", ("derivative", "nvic_priority_bits"),
                  f"core {info.core} has a VIM, not an NVIC: the priority is the channel number, there are no "
                  f"NVIC priority bits (describe vim.channels instead)")
    if vim is not None:
        seen: dict[int, int] = {}
        for i, fixed in enumerate(vim.fixed):
            where = ("vim", "fixed", i, "channel")
            if fixed.channel >= vim.channels:
                rep.error("derivative", where,
                          f"channel {fixed.channel} ({fixed.request}) does not exist: {info.name} has VIM channels "
                          f"0..{vim.channels - 1}")
            if fixed.channel in seen:
                rep.error("derivative", where,
                          f"channel {fixed.channel} is already fixed (vim.fixed[{seen[fixed.channel]}])")
            seen.setdefault(fixed.channel, i)
        if len(seen) >= vim.channels:
            rep.error("derivative", ("vim", "fixed"),
                      f"all {vim.channels} VIM channels of {info.name} are fixed, none is left for interrupt ranks")


# PLAT-10: NVIC priority bits a core can implement, PLAT-21: from the core table (config.CORES, one source).
# Vendor headers may disagree (S32K116.h: __NVIC_PRIO_BITS 4, S32K116_features.h: 2). ARCH-23: NVIC cores only.
NVIC_PRIORITY_BITS = {name: core.nvic_priority_bits for name, core in CORES.items()
                      if core.interrupt_controller == "nvic"}


def _derivative(cfg: Config, rep: Reporter) -> Patterns:
    d = cfg.derivative
    if cfg.derivative_file.stem != d.derivative.name:
        rep.error("derivative", ("derivative", "name"),
                  f"name {d.derivative.name} does not match the file name {cfg.derivative_file.name}")
    if cfg.derivative_file.parent.name != d.derivative.family:
        rep.error("derivative", ("derivative", "family"),
                  f"family {d.derivative.family} does not match the folder {cfg.derivative_file.parent.name}")
    _core(cfg, rep)
    if d.derivative.example:
        rep.warning("derivative", ("derivative", "example"),
                    f"{d.derivative.name} is a format example of the derivative database, not confirmed data")
    pin = _regex(rep, ("derivative", "pin_pattern"), d.derivative.pin_pattern)
    adc = {}
    for name, inst in d.instances.items():
        where = ("instances", name)
        if inst.class_ == "timer" and inst.channels is None:
            rep.error("derivative", where, "timer instance needs channels")
        if inst.class_ == "gpio" and inst.channels is None:              # PLAT-70
            rep.error("derivative", where, "gpio instance needs channels (number of pins of the port)")
        if inst.class_ == "adc":
            if inst.resolution_bits is None or inst.channel_pattern is None:
                rep.error("derivative", where, "adc instance needs resolution_bits and channel_pattern")
            else:
                compiled = _regex(rep, where + ("channel_pattern",), inst.channel_pattern)
                if compiled:
                    adc[name] = compiled
    _memory(cfg, rep)
    _clock_facts(cfg, rep)
    for package in d.pinmux:
        if package not in d.derivative.packages:
            rep.error("derivative", ("pinmux", package), f"pinmux for unknown package {package} "
                      f"(packages: {', '.join(d.derivative.packages)})")
    described = described_derivative(cfg, rep)
    if described is not None:
        missing = [n for n in d.instances if n not in described.instances]
        if missing:
            rep.error("derivative", ("device_header", "describes"), f"device header of {described.derivative.name} "
                      f"lacks instances {', '.join(missing)}")

    mux = pinmux(cfg, rep)
    if mux is not None and pin is not None:
        for signal, pins in mux.signals.items():
            for name in pins:
                if not pin.fullmatch(name):
                    level, base = ("derivative", ("pinmux", mux.package, "signals")) \
                        if d.pinmux[mux.package].signals is not None else ("pinmux", ("signals",))
                    rep.error(level, base + (signal, name), f"invalid pin name '{name}' for {d.derivative.name}")
    if mux is None and cfg.board.board.package not in d.pinmux:
        rep.warning("board", ("board", "package"), f"{d.derivative.name} has no pin multiplexing data for package "
                    f"{cfg.board.board.package}: pin functions are not checked")
    elif mux is not None and not mux.complete:
        rep.warning("board", ("board", "package"), f"pin multiplexing data of {d.derivative.name} "
                    f"{mux.package} is partial: pin functions are not checked")
    return Patterns(pin, adc, mux)


def _function(cfg: Config, rep: Reporter, patterns: Patterns, level: str, path, pin: str, signal: str):
    """Pin function possible according to the derivative's pin multiplexing (board package)."""
    mux = patterns.pinmux
    if mux is None or not mux.complete or (patterns.pin is not None and not patterns.pin.fullmatch(pin)):
        return                                      # partial data proves possible functions, not impossible ones
    name = f"{cfg.derivative.derivative.name} {mux.package}"
    if pin not in mux.pins:
        rep.error(level, path, f"{name} has no pin {pin}")
    elif mux.selector(signal, pin) is None:
        rep.error(level, path, f"{name} has no function {signal} on {pin} "
                  f"(available: {', '.join(mux.functions(pin)) or '-'})")


# ----------------------------------------------------------------------------------------------- board


def _board(cfg: Config, rep: Reporter, patterns: Patterns):
    b, d = cfg.board, cfg.derivative
    if b.board.package not in d.derivative.packages:
        rep.error("board", ("board", "package"), f"{d.derivative.name} has no package {b.board.package} "
                  f"(available: {', '.join(d.derivative.packages)})")

    def instance(path, name: str, classes: tuple[str, ...]):
        inst = d.instances.get(name)
        if inst is None:
            options = sorted(n for n, i in d.instances.items() if i.class_ in classes)
            rep.error("board", path, f"{d.derivative.name} has no instance {name} (available: {', '.join(options)})")
        elif inst.class_ not in classes:
            rep.error("board", path, f"{name} is a {inst.class_} instance, expected {' or '.join(classes)}")
        return inst if inst is not None and inst.class_ in classes else None

    def pin(path, value: str, signal: str | None = None):
        if patterns.pin and not patterns.pin.fullmatch(value):
            rep.error("board", path, f"invalid pin name '{value}' for {d.derivative.name}")
        elif signal is not None:
            _function(cfg, rep, patterns, "board", path, value, signal)

    def timer_channel(path, timer, inst_name: str, channel: int) -> bool:
        if timer is None or timer.channels is None:
            return False
        first = timer.first_channel
        if not first <= channel < first + timer.channels:
            rep.error("board", path, f"{inst_name} has channels {first}..{first + timer.channels - 1}")
            return False
        return True

    # names: signals (become pin/channel identifiers), PWM groups and pairs (RTE targets), devices
    namespaces: dict[str, dict[str, tuple]] = {"signal": {}, "pwm": {}, "device": {}}

    def name(space: str, path, value: str):
        key = value.upper()
        if key in namespaces[space]:
            rep.error("board", path, f"duplicate name {value} (also at {_fmt(namespaces[space][key])})")
        else:
            namespaces[space][key] = path

    for i, dev in enumerate(b.devices):
        name("device", ("devices", i, "name"), dev.name)
    devices = {dev.name for dev in b.devices}

    bus_class = {"spi": ("spi",), "can": ("can",), "uart": ("uart",), "i2c": ("i2c", "flexio")}
    for kind in BUS_KINDS:
        for i, bus in enumerate(getattr(b, kind)):
            base = (kind, i)
            name("signal", base + ("name",), bus.name)
            inst = instance(base + ("instance",), bus.instance, bus_class[kind])
            valid = inst is not None
            if kind == "i2c" and valid:
                valid = _flexio_pins(rep, base, bus, inst.class_)
            for role in BUS_PINS:
                if hasattr(bus, role):
                    pin(base + (role, "pin"), getattr(bus, role).pin,
                        bus_signal(cfg, kind, bus, role) if valid else None)
    for i, bus in enumerate(b.spi):
        if bus.device is not None and bus.device not in devices:
            rep.error("board", ("spi", i, "device"), f"unknown device {bus.device}")
    for i, bus in enumerate(b.can):
        if bus.phy is not None and bus.phy not in devices:
            rep.error("board", ("can", i, "phy"), f"unknown device {bus.phy}")

    for i, ch in enumerate(b.adc):
        base = ("adc", i)
        name("signal", base + ("name",), ch.name)
        signal = None
        if instance(base + ("instance",), ch.instance, ("adc",)) and ch.instance in patterns.adc_channel:
            if not patterns.adc_channel[ch.instance].fullmatch(ch.channel):
                rep.error("board", base + ("channel",), f"unknown channel '{ch.channel}' of {ch.instance} "
                          f"(pattern {patterns.adc_channel[ch.instance].pattern})")
            else:
                signal = adc_signal(ch.instance, ch.channel)
        pin(base + ("pin",), ch.pin, signal)

    for g, grp in enumerate(b.pwm):
        base = ("pwm", g)
        name("pwm", base + ("name",), grp.name)
        timer = instance(base + ("instance",), grp.instance, ("timer",))
        channels: dict[str, int] = {}
        numbers: dict[int, str] = {}
        for c, ch in enumerate(grp.channels):
            where = base + ("channels", c)
            name("signal", where + ("name",), ch.name)
            valid = timer_channel(where + ("channel",), timer, grp.instance, ch.channel)
            pin(where + ("pin",), ch.pin, timer_signal(grp.instance, ch.channel) if valid else None)
            if ch.channel in numbers:
                rep.error("board", where + ("channel",), f"channel {ch.channel} used twice ({numbers[ch.channel]})")
            numbers.setdefault(ch.channel, ch.name)
            channels[ch.name] = c
        paired: dict[str, str] = {}
        pairs_ok = True
        for p, pair in enumerate(grp.pairs):
            where = base + ("pairs", p)
            name("pwm", where + ("name",), pair.name)
            for side in ("high_side", "low_side"):
                ref = getattr(pair, side)
                if ref not in channels:
                    rep.error("board", where + (side,), f"{side} {ref} is not in channels of {grp.name}")
                    pairs_ok = False
                elif ref in paired:
                    rep.error("board", where + (side,), f"channel {ref} is already used by pair {paired[ref]}")
                    pairs_ok = False
                else:
                    paired[ref] = pair.name
        unpaired = [c for c in channels if c not in paired]
        if unpaired and pairs_ok:
            rep.error("board", base + ("pairs",), f"channels {', '.join(unpaired)} belong to no pair")
        if grp.gate_driver is not None:
            # IO-25: the role gate_driver of the device's module is checked with the modules (integrate)
            if grp.gate_driver not in devices:
                rep.error("board", base + ("gate_driver",), f"unknown device {grp.gate_driver}")
            else:
                other = next((o.name for o in b.pwm[:g] if o.gate_driver == grp.gate_driver), None)
                if other is not None:
                    rep.error("board", base + ("gate_driver",), f"device {grp.gate_driver} is already the gate "
                              f"driver of PWM group {other}")

    for i, ic in enumerate(b.input_capture):
        base = ("input_capture", i)
        name("signal", base + ("name",), ic.name)
        timer = instance(base + ("instance",), ic.instance, ("timer",))
        valid = timer_channel(base + ("channel",), timer, ic.instance, ic.channel)
        pin(base + ("pin",), ic.pin, timer_signal(ic.instance, ic.channel) if valid else None)

    for direction in ("inputs", "outputs"):
        for i, io in enumerate(getattr(b.dio, direction)):
            base = ("dio", direction, i)
            name("signal", base + ("name",), io.name)
            pin(base + ("pin",), io.pin, GPIO)


# ----------------------------------------------------------------------------------------------- project


def _board_names(cfg: Config, kind: str) -> list[str]:
    if kind == "dio":
        return [io.name for io in cfg.board.dio.inputs + cfg.board.dio.outputs]
    return [e.name for e in getattr(cfg.board, kind)]


def _project(cfg: Config, rep: Reporter, patterns: Patterns):
    p, d = cfg.project, cfg.derivative
    for kind in SELECTION_KINDS:
        selection = getattr(p.use, kind)
        if selection == "all":
            continue
        available = _board_names(cfg, kind)
        seen = set()
        for i, entry in enumerate(selection):
            if entry not in available:
                rep.error("project", ("use", kind, i), f"board {cfg.board.board.name} has no {kind} entry {entry} "
                          f"(available: {', '.join(available) or '-'})")
            if entry in seen:
                rep.warning("project", ("use", kind, i), f"{entry} selected twice")
            seen.add(entry)

    groups = [g.name for g in cfg.board.pwm]
    for key in p.pwm:
        if key not in groups:
            rep.error("project", ("pwm", key), f"board {cfg.board.board.name} has no PWM group {key} "
                      f"(available: {', '.join(groups) or '-'})")

    adc_names = {c.name.upper() for c in cfg.selected("adc")}
    for i, ch in enumerate(p.adc_internal):
        base = ("adc_internal", i)
        inst = d.instances.get(ch.instance)
        if inst is None or inst.class_ != "adc":
            options = sorted(n for n, x in d.instances.items() if x.class_ == "adc")
            rep.error("project", base + ("instance",), f"{d.derivative.name} has no ADC instance {ch.instance} "
                      f"(available: {', '.join(options)})")
        elif ch.channel not in inst.internal_channels:
            rep.error("project", base + ("channel",), f"unknown internal channel '{ch.channel}' of {ch.instance} "
                      f"(available: {', '.join(inst.internal_channels) or '-'})")
        if ch.name.upper() in adc_names:
            rep.error("project", base + ("name",), f"duplicate ADC channel name {ch.name}")
        adc_names.add(ch.name.upper())
    if (cfg.selected("adc") or p.adc_internal) and cfg.board.board.adc_reference_mv is None:
        rep.error("project", ("use", "adc"), f"ADC channels are used, but board {cfg.board.board.name} "
                  "defines no board.adc_reference_mv")

    if p.safety is not None:
        outputs = {io.name for io in cfg.selected("dio.outputs")}
        for key in ("safe_state_output", "locked_pin"):
            value = getattr(p.safety, key)
            if value not in outputs:
                rep.error("project", ("safety", key), f"'{value}' must be a digital output used by the project")
        if patterns.pin and not patterns.pin.fullmatch(p.safety.test_pin):
            rep.error("project", ("safety", "test_pin"), f"invalid pin name '{p.safety.test_pin}'")
        else:
            _function(cfg, rep, patterns, "project", ("safety", "test_pin"), p.safety.test_pin, GPIO)

    if cfg.rte_file is not None and not cfg.rte_file.is_file():
        rep.error("project", ("project", "rte"), f"ECU description {cfg.rte_file} not found")
    if cfg.diag_file is not None and not cfg.diag_file.is_file():
        rep.error("project", ("project", "diag"), f"diagnostic description {cfg.diag_file} not found")

    _can_message_buffers(cfg, rep)
    _can_bus_off(cfg, rep)                     # COM-37
    _interrupts(cfg, rep)


# ARCH-42: message buffers the CAN controller driver of a family needs on every instance it serves, with the
# driver's name and buffer layout for the message. s32k1xx: until COM-26 FlexCANDevice of OpenBSW received in
# buffers 0..15 and transmitted from 16..31 on every instance - buffer 16 of an instance with 16 buffers (S32K142
# FlexCAN1, S32K144 FlexCAN1/2, S32K146 FlexCAN2, mcudb message_buffers) is unimplemented address space, the ECU
# stopped in a BusFault when CAN started (COM-05). COM-26: the family port's copy
# (platforms/s32k1xx/port/bspConfiguration/bspFlexCan) splits the buffers of each instance (receive below n/2,
# transmit from n/2, callback frame in n-1; tools/bswgen can_buffer_split) and needs at least 8
# (bswgen CAN_MIN_MESSAGE_BUFFERS), so every S32K1xx instance is served. Families without an entry are not checked.
FLEXCAN_DRIVER_MESSAGE_BUFFERS = 8
CAN_DRIVER_MESSAGE_BUFFERS = {
    "s32k1xx": (FLEXCAN_DRIVER_MESSAGE_BUFFERS,
                "FlexCANDevice of the family port receives in the lower half and transmits from the upper half"),
}


def can_message_buffers_problem(cfg: Config, bus) -> str | None:
    """Text if the instance of a used CAN bus has fewer message buffers than the family driver needs, else None.

    bswgen repeats it word for word as its last line of defence (ARCH-42). An instance without message_buffers in
    mcudb (unknown) is not rejected.
    """
    d = cfg.derivative
    need = CAN_DRIVER_MESSAGE_BUFFERS.get(d.derivative.family)
    inst = d.instances.get(bus.instance)
    if need is None or inst is None or inst.class_ != "can" or inst.message_buffers is None:
        return None
    count, layout = need
    if inst.message_buffers >= count:
        return None
    return (f"CAN bus {bus.name} is on {bus.instance}, which has {inst.message_buffers} message buffers on the "
            f"{d.derivative.name}; the CAN driver of {d.derivative.family} needs {count} ({layout}): the first "
            f"access to buffer {inst.message_buffers} is an access error and the ECU stops when CAN starts - use an "
            f"instance with {count} buffers or leave {bus.name} out of use.can (the bootloader may still use it)")


def _can_message_buffers(cfg: Config, rep: Reporter):
    """ARCH-42: a used CAN bus on an instance with too few message buffers for the family driver is an error.

    Path: the entry of the bus in use.can (use.can when it is 'all'), because that is where the project decides.
    """
    selection = cfg.project.use.can
    for bus in cfg.selected("can"):
        text = can_message_buffers_problem(cfg, bus)
        if text is None:
            continue
        path = ("use", "can") if selection == "all" else ("use", "can", list(selection).index(bus.name))
        rep.error("project", path, text)


# TOOL-120: instance classes the I2C driver of a family port runs on, with the driver for the message. s32k1xx: the
# only I2C driver is the FlexIO I2C master (platforms/s32k1xx/bsp/bspFlexioI2c); there is no LPI2C driver, and
# bswgen muxes the pins of an I2C bus only on a FlexIO. The schema allows both classes (an LPI2C is a fact of the
# derivative, a board may describe it), a *used* bus on an instance without a driver cannot be built. Families
# without an entry are not checked (like CAN_DRIVER_MESSAGE_BUFFERS).
I2C_DRIVERS = {
    "s32k1xx": (("flexio",), "the FlexIO I2C master (platforms/s32k1xx/bsp/bspFlexioI2c)"),
}


def i2c_driver_problem(cfg: Config, bus) -> str | None:
    """Text if a used I2C bus sits on an instance the I2C driver of the family port does not run on, else None.

    bswgen repeats it word for word as its last line of defence.
    """
    d = cfg.derivative
    family = d.derivative.family
    driver = I2C_DRIVERS.get(family)
    inst = d.instances.get(bus.instance)
    if driver is None or inst is None:
        return None
    classes, text = driver
    if inst.class_ in classes:
        return None
    offers = sorted(n for n, x in d.instances.items() if x.class_ in classes)
    index = next(i for i, b in enumerate(cfg.board.i2c) if b.name == bus.name)
    return (f"I2C bus {bus.name} is on {bus.instance} (class {inst.class_}); the family port {family} has no "
            f"driver for it - its I2C driver is {text} and runs only on {', '.join(offers) or 'a FlexIO'} of "
            f"{d.derivative.name}: put the bus on one of them (board.yaml i2c[{index}].instance, with flexio_pin "
            f"for SDA and SCL) or leave {bus.name} out of use.i2c")


def _i2c_drivers(cfg: Config, rep: Reporter, repo: Path = REPO):
    """TOOL-120: a used I2C bus without a driver of the family port is an error in use.i2c (use.i2c when 'all').

    Only for a project that is built with the family port (built_with_family_port, as PLAT-95): a configuration-
    only profile is never compiled, and the clock tests of tools/nxconfig use an LPI2C on purpose.
    """
    if not cfg.board.i2c or not built_with_family_port(cfg, repo):
        return
    selection = cfg.project.use.i2c
    for bus in cfg.selected("i2c"):
        text = i2c_driver_problem(cfg, bus)
        if text is not None:
            path = ("use", "i2c") if selection == "all" else ("use", "i2c", list(selection).index(bus.name))
            rep.error("project", path, text)


# ------------------------------------------------------------------------------------- ECU description required
# TOOL-114 (user decision 2026-09-25, variant A): the RTE is not optional. The root CMakeLists.txt runs rtegen at
# every configure and aborts without project.rte, so a project without one could pass `nxconfig check` and the
# Studio and still never build. nxconfig now says so first, with file and YAML path.
#
# The rule applies where a project is BUILT - the same marker as PLAT-95 and TOOL-120 (built_with_family_port):
# the family has a port template and the board is a real board of the workspace or of the tree. Every project of
# projects/ and every project the wizard creates on a board of boards/ meets it - projects/nucleo_f446re_demo too:
# the stm32 port template exists (a stub that stops the configure step with the list of its missing parts), so the
# project has an ECU description with one task and no component. What the marker leaves out is exactly the
# configuration-only test profiles of tools/nxconfig, tools/bswgen, tools/studio and tools/rtegen: their boards lie
# next to their tests and nothing is ever compiled from them (a compile stops at the missing platform or port
# template of testfam/rm4x, or at a board no boards/ folder holds). They are not left out silently: rtegen.validate()
# - Studio "Konfiguration prüfen", `studio validate --like-generate`/`--full` - reports the missing description as
# an error for every project, because "Generieren" runs rtegen and refuses it (tools/nxconfig/test_nxconfig.py
# RteRequiredTest pins both sides).

def _default_task_text(repo: Path) -> str:
    """The one task of an rte.yaml without components, as the wizard writes it (application_default)."""
    try:
        data = YAML(typ="safe").load(tasks_manifest(repo).read_text(encoding="utf-8"))
        default = data["application_default"]
        name, priority, stack = default["name"], default["priority"], default["stack_bytes"]
    except (OSError, YAMLError, TypeError, KeyError):
        name, priority, stack = "swc", "<priority>", "<stack_bytes>"
    return f"{{ name: {name}, period_ms: 10, priority: {priority}, stack_bytes: {stack} }}"


def rte_required(cfg: Config, rep: Reporter, repo: Path = REPO):
    """TOOL-114: project.rte must be set in a project that is built (section comment above)."""
    if cfg.rte_file is not None or not built_with_family_port(cfg, repo):
        return
    rep.error("project", ("project", "rte"),
              "project.rte (ECU description) is not set: the build runs rtegen at configure time and aborts "
              "without one - the RTE is required (TOOL-114). To fix: add `rte: rte.yaml` to the project section "
              "and an rte.yaml next to project.yaml with one task and no component yet (schema_version: 2, "
              f"tasks: [{_default_task_text(repo)}], components: []), or create the project with the wizard "
              "(nxconfig new, Studio: Neues Projekt), which always writes one")


# ------------------------------------------------------------------------------------- interrupt priorities (ranks)


def _source_names(cfg: Config, source: str) -> tuple[list[str], list[str]]:
    """(entries of the board, entries the project uses) of one interrupt source (model.InterruptSource)."""
    if source == "pwm":
        return [grp.name for grp in cfg.board.pwm], [grp.name for grp, _ in cfg.selected_pwm()]
    if source == "dio":
        return (_board_names(cfg, "dio"),
                [io.name for io in cfg.selected("dio.inputs") + cfg.selected("dio.outputs")])
    if source == "sample_group":                   # IO-62: a project entry, not a board entry
        names = list(cfg.project.sample_groups)
        return names, names
    return _board_names(cfg, source), [entry.name for entry in cfg.selected(source)]


@dataclass(frozen=True)
class InterruptPriorities:
    """Checked interrupt priorities of a project, for the generators (level = max_syscall_level + rank)."""

    bits: int                                       # derivative.nvic_priority_bits
    max_syscall_level: int                          # project or default of the core architecture
    ranks: dict[tuple[str, str], int]               # (source, board name) -> rank

    @property
    def rank_count(self) -> int:
        return (1 << self.bits) - self.max_syscall_level

    def level(self, source: str, name: str) -> int:
        return self.max_syscall_level + self.ranks[(source, name)]


def interrupt_priorities(cfg: Config) -> InterruptPriorities | None:
    """NVIC interrupt priorities of a checked configuration; None if the derivative has no nvic_priority_bits or
    the core has no NVIC (VIM cores: vim_channel_order)."""
    bits = cfg.derivative.derivative.nvic_priority_bits
    core = CORES.get(cfg.derivative.derivative.core)
    if bits is None or core is None or core.interrupt_controller != "nvic":
        return None
    use = cfg.project.interrupts
    level = core.max_syscall_default if use.max_syscall_level is None else use.max_syscall_level
    return InterruptPriorities(bits, level, {(r.source, r.name): r.rank for r in use.ranks})


@dataclass(frozen=True)
class VimChannelOrder:
    """ARCH-23: checked interrupt ranks of a project on a VIM core, for the family generator.

    The priority of a VIM channel is its number (0 highest), so a rank does not become a level but a position: the
    ranked entries in order of rank (equal ranks in YAML order) get the channels that are not fixed, from the
    lowest number upwards. Every entry is an IRQ and may call the RTOS; FIQs are no ranks. How many requests an
    instance has (and so how many channels it takes) is known only to the family generator; `order` is what it
    distributes over `free_channels`.
    """

    channels: int                                   # derivative vim.channels
    fixed: tuple[int, ...]                          # derivative vim.fixed[].channel, ascending
    ranks: dict[tuple[str, str], int]               # (source, board name) -> rank
    order: tuple[tuple[str, str], ...]              # (source, board name) from the most urgent rank on

    @property
    def free_channels(self) -> tuple[int, ...]:
        return tuple(c for c in range(self.channels) if c not in self.fixed)

    def position(self, source: str, name: str) -> int:
        return self.order.index((source, name))


def _vim_order(ranks) -> tuple[tuple[str, str], ...]:
    indexed = sorted(enumerate(ranks), key=lambda item: (item[1].rank, item[0]))
    order: list[tuple[str, str]] = []
    for _, r in indexed:
        if (r.source, r.name) not in order:
            order.append((r.source, r.name))
    return tuple(order)


def vim_channel_order(cfg: Config) -> VimChannelOrder | None:
    """VIM channel order of a checked configuration; None if the core has no VIM or the derivative no vim entry."""
    core = CORES.get(cfg.derivative.derivative.core)
    vim = cfg.derivative.vim
    if core is None or core.interrupt_controller != "vim" or vim is None:
        return None
    ranks = cfg.project.interrupts.ranks
    return VimChannelOrder(vim.channels, tuple(sorted({f.channel for f in vim.fixed})),
                           {(r.source, r.name): r.rank for r in ranks}, _vim_order(ranks))


def _interrupts(cfg: Config, rep: Reporter):
    """PLAT-21: a rank per interrupt source instead of an NVIC value (platforms/common/interruptPriority).

    Rank 0 is the most urgent interrupt that may call the RTOS. NVIC: the level follows as max_syscall_level + rank.
    VIM (ARCH-23): the ranks order the free channels, there is no syscall level. A rank the derivative cannot
    represent is an error here (with YAML path), not a silently wrapped priority at runtime.
    """
    use, info = cfg.project.interrupts, cfg.derivative.derivative
    core = CORES.get(info.core)
    if core is None:
        return                                      # unknown core: already reported by _core
    if core.interrupt_controller == "vim":
        _vim_interrupts(cfg, rep, core)
        return
    bits = info.nvic_priority_bits
    if bits is None:
        if use.max_syscall_level is not None or use.ranks:
            rep.error("derivative", ("derivative", "nvic_priority_bits"),
                      f"{info.name} has no nvic_priority_bits, but project {cfg.project.project.name} describes "
                      "interrupt priorities (interrupts)")
        return
    _can_ranks(cfg, rep)
    levels = 1 << bits
    max_syscall = core.max_syscall_default if use.max_syscall_level is None else use.max_syscall_level
    if core.architecture == "armv6m":
        if max_syscall != 0:
            rep.error("project", ("interrupts", "max_syscall_level"),
                      f"core {info.core} has no BASEPRI: the RTOS masks every interrupt (PRIMASK) and "
                      f"max_syscall_level must be 0, not {max_syscall}")
            return
    elif not 1 <= max_syscall <= levels - 1:
        rep.error("project", ("interrupts", "max_syscall_level"),
                  f"max_syscall_level {max_syscall} must be 1..{levels - 1} on core {info.core} "
                  f"({bits} NVIC priority bits of {info.name}; 0 would switch the RTOS masking off)")
        return
    available = levels - max_syscall
    for i, entry in _rank_entries(cfg, rep):
        where = ("interrupts", "ranks", i)
        if entry.rank >= available:
            rep.error("project", where + ("rank",),
                      f"rank {entry.rank} of {entry.source} {entry.name} does not fit: {bits} NVIC priority bits "
                      f"of {info.name} and max_syscall_level {max_syscall} leave ranks 0..{available - 1} "
                      f"(NVIC levels {max_syscall}..{levels - 1})")


def can_rank_missing(bus: str) -> str:
    """Text of a used CAN bus without interrupt rank; bswgen repeats it word for word as its last line of defence."""
    return (f"CAN bus {bus} is used, but has no interrupt rank: the board port cannot set the level of its message "
            f"buffer interrupts (interrupts.ranks: {{source: can, name: {bus}, rank: <n>}})")


def _can_ranks(cfg: Config, rep: Reporter):
    """ARCH-33: every used CAN bus needs an interrupt rank (ARCH-07, PLAT-21) on a derivative that has ranks at all.

    CanSystem enables the message buffer interrupts of every bus, so the board port needs the level of each one;
    without a rank it stops at the undefined macro NX_IRQ_LEVEL_CAN_<bus>. An error here, before any generator runs,
    as projects/README.md documents it. The path is interrupts.ranks, where the entry belongs; a project without an
    interrupts section gets the path of the bus entry (can.<bus>, required for every used bus), so that the message
    still has a line.
    """
    ranked = {r.name for r in cfg.project.interrupts.ranks if r.source == "can"}
    src = getattr(cfg, "sources", {}).get("project")
    tree = src.tree if src is not None else None
    path = ("interrupts", "ranks") if isinstance(tree, dict) and "interrupts" in tree else None
    for bus in cfg.selected("can"):
        if bus.name not in ranked:
            rep.error("project", path or ("can", bus.name), can_rank_missing(bus.name))


def _rank_entries(cfg: Config, rep: Reporter):
    """Rank entries of the project with their index; reports unknown, unused and duplicate entries (any controller)."""
    seen: dict[tuple[str, str], tuple] = {}
    for i, entry in enumerate(cfg.project.interrupts.ranks):
        where = ("interrupts", "ranks", i)
        known, used = _source_names(cfg, entry.source)
        if entry.name not in known and entry.source == "sample_group":
            rep.error("project", where + ("name",),
                      f"the project has no sample group {entry.name} (sample_groups: {', '.join(known) or '-'})")
        elif entry.name not in known:
            rep.error("project", where + ("name",),
                      f"board {cfg.board.board.name} has no {entry.source} entry {entry.name} "
                      f"(available: {', '.join(known) or '-'})")
        elif entry.name not in used:
            # A rank for an entry the project does not use costs nothing (an unused macro), but is usually a leftover
            rep.warning("project", where + ("name",),
                        f"rank for {entry.source} {entry.name}, which the project does not use (use.{entry.source})")
        key = (entry.source, entry.name)
        if key in seen:
            rep.error("project", where + ("name",),
                      f"{entry.source} {entry.name} already has a rank ({_fmt(seen[key])})")
        seen[key] = where + ("name",)
        yield i, entry


def _vim_interrupts(cfg: Config, rep: Reporter, core):
    """ARCH-23: ranks on a VIM core. The ranks order the channels that are not fixed; no syscall level (IRQ may call
    the RTOS, FIQ never). More ranked entries than free channels cannot be placed (one channel per entry is the
    least an entry needs; instances with several requests are checked by the family generator)."""
    use, info, vim = cfg.project.interrupts, cfg.derivative.derivative, cfg.derivative.vim
    if use.max_syscall_level is not None:
        rep.error("project", ("interrupts", "max_syscall_level"),
                  f"core {info.core} has a VIM: an interrupt that may call the RTOS is an IRQ, an FIQ never does, "
                  f"so there is no syscall level (remove max_syscall_level, not {use.max_syscall_level})")
    if vim is None:
        if use.ranks:
            rep.error("derivative", ("vim",),
                      f"{info.name} has no vim (channel count, fixed channels), but project "
                      f"{cfg.project.project.name} describes interrupt priorities (interrupts)")
        return
    _can_ranks(cfg, rep)
    for _ in _rank_entries(cfg, rep):
        pass
    free = vim.channels - len({f.channel for f in vim.fixed if f.channel < vim.channels})
    order = _vim_order(use.ranks)
    first = {}
    for i, r in enumerate(use.ranks):
        first.setdefault((r.source, r.name), i)
    for source, name in order[max(free, 0):]:       # the least urgent entries are the ones left without a channel
        rep.error("project", ("interrupts", "ranks", first[(source, name)], "rank"),
                  f"{source} {name} gets no channel: {info.name} has {vim.channels} VIM channels, "
                  f"{vim.channels - free} of them fixed, which leaves {free} for {len(order)} ranked entries")


# ----------------------------------------------------------------------------------------------- PWM counter


@dataclass(frozen=True)
class PwmCounterLimits:
    """Counter of the complementary PWM timer of a family: what it can count, not how it is encoded (ARCH-33).

    The register encodings (prescaler field, DTPS code) stay in the family generator; the limits are here so that
    nxconfig rejects a frequency or dead time the timer cannot produce, with the YAML path of the project value.
    """

    timer: str                                      # timer IP in messages, e.g. FlexTimer
    instance: re.Pattern                            # instances the family PWM driver drives
    max_period_ticks: int                           # counter ticks per period (MOD + 1)
    min_period_ticks: int                           # duty resolution the driver guarantees
    prescalers: int                                 # prescaler 1 << 0 .. 1 << (prescalers - 1)
    max_deadtime_ticks: int                         # dead time counter
    deadtime_dividers: tuple[int, ...]              # dead time prescaler, smallest first


# s32k1xx: FlexTimer, MOD + 1 <= 0xFFFF (100 % duty uses CnV = MOD + 1, 16 bit), at least 100 ticks (1 % duty),
# prescaler 1..128, DEADTIME.DTVAL 6 bit with DTPS 1/4/16 (RM FTM chapter; driver bsp/io/outputPwm)
PWM_COUNTERS = {
    "s32k1xx": PwmCounterLimits("FlexTimer", re.compile(r"^FTM\d$"), 0xFFFF, 100, 8, 63, (1, 4, 16)),
}


@dataclass(frozen=True)
class PwmTiming:
    """Counter values of one PWM group: period = period_ticks << prescaler_shift counter clock periods."""

    prescaler_shift: int
    period_ticks: int
    frequency_hz: Fraction                          # actual frequency
    deadtime_divider: int
    deadtime_ticks: int                             # dead time = deadtime_ticks * deadtime_divider clock periods
    # IO-61: up-down counting (alignment center): the counter turns at counter_top, period_ticks = 2 * counter_top
    center: bool = False

    @property
    def counter_top(self) -> int:
        """Highest counter value (MOD with the counter starting at 0): edge period - 1, center half the period."""
        return self.period_ticks // 2 if self.center else self.period_ticks - 1


class PwmTimingError(ValueError):
    """A frequency or dead time the counter cannot produce; `key` is the project value (pwm.<group>.<key>)."""

    def __init__(self, key: str, text: str):
        super().__init__(text)
        self.key = key


def _hz(value: Fraction) -> str:
    return str(value.numerator) if value.denominator == 1 else f"{float(value):.3f}"


def pwm_timing(limits: PwmCounterLimits, group: str, instance: str, clock_hz: Fraction | int,
               frequency_hz: int, deadtime_ns: int, center: bool = False) -> PwmTiming:
    """Period and dead time of a PWM group in counter ticks; raises PwmTimingError with the text of the finding.

    Single rule for nxconfig (message with YAML path) and the family generator (register values, last line of
    defence with the same text). Period: smallest prescaler (best duty resolution) whose tick count fits. Dead time:
    counter clock divided by the smallest divider that fits, rounded up (never shorter than requested); two dead
    times per period must leave something of the period.

    `center` (IO-61, alignment center): the counter counts up to its top and down again, so a period is twice the
    top; the top (not the period) is what the counter limits and the duty resolution apply to. Same prescaler rule,
    the same dead time.
    """
    clock = Fraction(clock_hz)
    where = f"PWM group {group}"
    if center:
        where += " (alignment center)"
    for shift in range(limits.prescalers):
        if center:
            # counter range 0..top..0; the 100 % compare value top + 1 has to fit like MOD + 1 of the edge counter
            top = round(clock / (2 * (frequency_hz << shift)))
            ticks, steps, span = 2 * top, top, top + 1
        else:
            ticks = round(clock / (frequency_hz << shift))
            steps = span = ticks
        if span <= limits.max_period_ticks:
            break
    else:
        raise PwmTimingError("frequency_hz", f"{where}: frequency {frequency_hz} Hz is too low for the counter clock "
                             f"{_hz(clock)} Hz of {instance} (largest prescaler {1 << (limits.prescalers - 1)}, at "
                             f"most {limits.max_period_ticks} ticks per "
                             f"{'counting direction' if center else 'period'})")
    if steps < limits.min_period_ticks:
        raise PwmTimingError("frequency_hz", f"{where}: frequency {frequency_hz} Hz leaves {steps} counter ticks per "
                             f"{'counting direction' if center else 'period'} at the counter clock {_hz(clock)} Hz of "
                             f"{instance}, at least {limits.min_period_ticks} needed (duty resolution)")
    actual = clock / (ticks << shift)
    for divider in limits.deadtime_dividers:
        dt_ticks = ceil(Fraction(deadtime_ns) * clock / (divider * 1_000_000_000))
        if dt_ticks <= limits.max_deadtime_ticks:
            break
    else:
        largest = limits.deadtime_dividers[-1]
        maximum = floor(Fraction(limits.max_deadtime_ticks * largest * 1_000_000_000) / clock)
        raise PwmTimingError("deadtime_ns", f"{where}: dead time {deadtime_ns} ns exceeds the {limits.timer} maximum "
                             f"of {maximum} ns ({limits.max_deadtime_ticks} x {largest} periods of the counter clock "
                             f"{_hz(clock)} Hz of {instance})")
    if 2 * dt_ticks * divider >= ticks << shift:
        raise PwmTimingError("deadtime_ns", f"{where}: dead time {deadtime_ns} ns must be shorter than half the PWM "
                             f"period ({float(1_000_000_000 / (2 * actual)):.0f} ns at {frequency_hz} Hz): two dead "
                             f"times per period")
    return PwmTiming(shift, ticks, actual, divider, dt_ticks, center)


def _pwm_timing(cfg: Config, rep: Reporter):
    """ARCH-33: frequency and dead time of every used PWM group against the family's PWM counter.

    Needs the clock plan (counter clock of the timer instance); a family without a counter model, an instance its
    driver does not drive or a counter clock that is no whole number are left to the family generator.
    """
    limits = PWM_COUNTERS.get(cfg.derivative.derivative.family)
    if limits is None or cfg.clock is None:
        return
    for grp, use in cfg.selected_pwm():
        if not limits.instance.match(grp.instance):
            continue
        clock = cfg.clock.counter_hz(grp.instance)
        if clock.denominator != 1:
            continue
        try:
            pwm_timing(limits, grp.name, grp.instance, clock, use.frequency_hz, use.deadtime_ns,
                       center=use.alignment == "center")
        except PwmTimingError as e:
            rep.error("project", ("pwm", grp.name, e.key), str(e))


# IO-31: averaging of a board ADC channel over one period of a PWM group (project adc_sampling.<channel>). Spacing of
# the conversions >= 1.5 conversion times, otherwise a warning: the caller needs part of a conversion for the call
# and the time source, and a late sample shifts the window (console "adcaccess stats"). The generator
# (tools/bswgen adc_sampling_plan) applies the same rule as its last line of defence and renders the table.
ADC_SAMPLING_SPACING_MARGIN = Fraction(3, 2)


def _adc_sampling(cfg: Config, rep: Reporter):
    """Averaged ADC channels: used and fitted board channel, PWM group of the project, samples fit into its period."""
    specs = cfg.project.adc_sampling
    if not specs:
        return
    channels = {ch.name: ch for ch in cfg.selected("adc")}
    internal = {ch.name for ch in cfg.project.adc_internal}
    limits = PWM_COUNTERS.get(cfg.derivative.derivative.family)
    for name, spec in specs.items():
        path = ("adc_sampling", name)
        ch = channels.get(name)
        if ch is None:
            why = ("an internal channel; its callers (safety checks) read it with the interrupts suspended"
                   if name in internal else "no board ADC channel the project uses (use.adc)")
            rep.error("project", path, f"adc_sampling {name}: {why}")
            continue
        if not ch.fitted:
            rep.error("project", path, f"adc_sampling {name}: the circuit of {name} is not fitted on board "
                      f"{cfg.board.board.name} (adc[].fitted: false)")
            continue
        grp = next((g for g, _ in cfg.selected_pwm() if g.name == spec.pwm_group), None)
        if grp is None:
            used = ", ".join(g.name for g, _ in cfg.selected_pwm()) or "-"
            rep.error("project", path + ("pwm_group",), f"adc_sampling {name}: PWM group {spec.pwm_group} is not "
                      f"used by the project (pwm: {used}), there is no period to average over")
            continue
        plan = cfg.adc_access
        if limits is None or cfg.clock is None or plan is None or not limits.instance.match(grp.instance):
            continue                                # no counter or converter model: left to the family generator
        clock = cfg.clock.counter_hz(grp.instance)
        use = cfg.project.pwm[grp.name]
        try:
            timing = pwm_timing(limits, grp.name, grp.instance, clock, use.frequency_hz, use.deadtime_ns,
                                center=use.alignment == "center")
        except PwmTimingError:
            continue                                # reported by _pwm_timing
        period_ns = Fraction(1_000_000_000) / timing.frequency_hz
        spacing = int(period_ns / spec.samples + Fraction(1, 2))
        conversion = plan.conversion_ns
        if spec.samples * conversion > period_ns:
            rep.error("project", path + ("samples",), f"adc_sampling {name}: {spec.samples} conversions of "
                      f"{conversion} ns do not fit into one period of {grp.name} ({float(period_ns):.0f} ns at "
                      f"{_hz(timing.frequency_hz)} Hz, at most {int(period_ns // conversion)})")
        elif spacing < conversion * ADC_SAMPLING_SPACING_MARGIN:
            rep.warning("project", path + ("samples",), f"adc_sampling {name}: {spec.samples} conversions of "
                        f"{conversion} ns leave {spacing - conversion} ns between two samples (spacing {spacing} "
                        f"ns); with the call overhead samples may come late, at most "
                        f"{int(period_ns / (conversion * ADC_SAMPLING_SPACING_MARGIN))} samples keep half a "
                        f"conversion free")


# ----------------------------------------------------------------------------------------------- resources


@dataclass
class Claim:
    owner: str
    path: tuple
    share: str | None


def _resources(cfg: Config, rep: Reporter) -> dict[str, Claim]:
    """Resources the project claims (pins, instances, channels), each once; returns them by resource name."""
    claims: dict[str, Claim] = {}

    def claim(resource: str, owner: str, path: tuple, share: str | None = None):
        first = claims.get(resource)
        if first is None:
            claims[resource] = Claim(owner, path, share)
        elif first.share is None or first.share != share:
            rep.error("project", path, f"{resource} is used by {owner} and by {first.owner}")

    for kind in BUS_KINDS:
        where = ("use", kind)
        for bus in cfg.selected(kind):
            # IO-28: I2C buses may share a FlexIO; tools/nxconfig/i2c.py hands out its shifters and timers per bus
            inst = cfg.derivative.instances.get(bus.instance)
            flexio = kind == "i2c" and inst is not None and inst.class_ == "flexio"
            claim(bus.instance, f"{kind} {bus.name}", where, share="i2c on flexio" if flexio else None)
            for role in BUS_PINS:
                if hasattr(bus, role):
                    claim(f"pin {getattr(bus, role).pin}", f"{kind} {bus.name} ({role})", where)
    for ch in cfg.selected("adc"):
        claim(f"pin {ch.pin}", f"adc {ch.name}", ("use", "adc"))
        claim(f"{ch.instance} {ch.channel}", f"adc {ch.name}", ("use", "adc"))
    for i, ch in enumerate(cfg.project.adc_internal):
        claim(f"{ch.instance} {ch.channel}", f"adc_internal {ch.name}", ("adc_internal", i))
    for grp, _ in cfg.selected_pwm():
        where = ("pwm", grp.name)
        claim(f"{grp.instance} counter", f"pwm {grp.name}", where, share=f"pwm {grp.name}")
        for ch in grp.channels:
            claim(f"pin {ch.pin}", f"pwm {grp.name} ({ch.name})", where)
            claim(f"{grp.instance} channel {ch.channel}", f"pwm {grp.name} ({ch.name})", where)
    for ic in cfg.selected("input_capture"):
        where = ("use", "input_capture")
        claim(f"{ic.instance} counter", f"input_capture {ic.name}", where, share="input_capture")
        claim(f"pin {ic.pin}", f"input_capture {ic.name}", where)
        claim(f"{ic.instance} channel {ic.channel}", f"input_capture {ic.name}", where)
    for direction in ("inputs", "outputs"):
        for io in cfg.selected(f"dio.{direction}"):
            claim(f"pin {io.pin}", f"dio {io.name}", ("use", "dio"))
    if cfg.project.safety is not None:
        claim(f"pin {cfg.project.safety.test_pin}", "safety test_pin", ("safety", "test_pin"))
    return claims


# ----------------------------------------------------------------------------------------------- modules

PASSIVE = "canPhyPassive"       # role provider of a CAN bus whose transceiver needs no driver (always ready)
# module.yaml connect kind -> board entries it may be wired to (Config.selected kind)
CONNECT_KINDS = {"digital_output": "dio.outputs", "digital_input": "dio.inputs", "analog_input": "adc"}
CONSOLE_PARAMETER = "console"   # parameter holding the command group of modules with console: instance
_NUMBER = r"-?\d+"
_OPERAND = r"([a-z][a-z0-9_]*|-?\d+)"
CONSTRAINT_RE = re.compile(rf"^\s*{_OPERAND}\s*(<=|>=|==|!=|<|>)\s*{_OPERAND}\s*$")
_COMPARE = {"<": int.__lt__, "<=": int.__le__, ">": int.__gt__, ">=": int.__ge__, "==": int.__eq__,
            "!=": int.__ne__}


@dataclass
class DeviceUse:
    """Board device the project instantiates through its module."""

    name: str
    part: str
    module: str
    spi: list[str]              # used SPI buses with this device
    can_phy: list[str]          # used CAN buses this device is the transceiver of
    connect: dict[str, str] = field(default_factory=dict)                   # connection -> board entry
    parameters: dict[str, int | bool | str] = field(default_factory=dict)   # project values and defaults
    verify: bool = False        # parameter values marked as not confirmed (modules.<device>.verify)
    # IO-25: used PWM group whose channels drive this gate driver (board pwm[].gate_driver) and its mode
    pwm: str | None = None
    pwm_mode: str | None = None
    # IO-25: (bus, instance, chip select index) of every used SPI bus in `spi`, same order
    spi_buses: list[tuple[str, str, int]] = field(default_factory=list)
    # COM-21: selective CAN wake-up (modules.<device>.can_wakeup_frame), checked, bit rate derived; None: any activity
    can_wakeup_frame: "CanWakeupFrameUse | None" = None
    # IO-33: checked wiring of the part's own pins (board devices[].pins): pin -> use; pins not described are missing
    pins: dict[str, str] = field(default_factory=dict)


@dataclass
class CanWakeupFrameUse:
    """Checked wake-up frame of a system basis chip (COM-21): project values, defaults and the derived bit rate."""

    bus: str                    # used CAN bus at the device's transceiver
    bitrate: int                # can.<bus>.bitrate of the project, bit/s
    id: int
    extended: bool
    compare_mask: int           # all identifier bits if left out
    dlc: int | None             # None: only the identifier is compared (PNDM = 0)
    data_mask: list[int]
    fd_passive: bool


@dataclass
class CanBusUse:
    name: str
    instance: str
    index: int                  # position among the used CAN buses (board order)
    phy: str | None             # board device acting as transceiver, None if not described
    module: str                 # module providing can_phy, or PASSIVE


@dataclass
class ModuleUse:
    """Module without a board device whose parameters the project sets (module.yaml project_parameters)."""

    module: str
    parameters: dict[str, int | bool | str] = field(default_factory=dict)   # project values and defaults
    verify: bool = False        # parameter values marked as not confirmed (modules.<module>.verify)
    # SYS-25: values derived from the parameters by a check (name -> (value, how it is derived)), generated as
    # NX_MODULE_<MODULE>_DERIVED_<NAME>; never set by the project
    derived: dict[str, tuple[int, str]] = field(default_factory=dict)


@dataclass
class Integration:
    devices: list[DeviceUse]
    can: list[CanBusUse]
    spi: list[str]
    dio: list[str]
    adc: list[str]
    messages: list[Message]
    modules: dict[str, ModuleUse] = field(default_factory=dict)             # by module name, in manifest order
    i2c: list[str] = field(default_factory=list)                            # used I2C buses (IO-28)

    def module_count(self, module: str) -> int:
        return sum(d.module == module for d in self.devices)

    def instances(self, module: str) -> list[DeviceUse]:
        return [d for d in self.devices if d.module == module]

    def parameters(self, module: str) -> dict[str, int | bool | str]:
        """Checked parameter values of a module with project_parameters (empty if it has none)."""
        use = self.modules.get(module)
        return dict(use.parameters) if use is not None else {}


def _value_problem(spec: ParameterSpec, value) -> str | None:
    """Why `value` does not fit the parameter, None if it does."""
    if spec.type == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            return f"expected an integer, got {value!r}"
        if not spec.min <= value <= spec.max:
            return f"{value} is out of range {spec.min}..{spec.max}" + (f" {spec.unit}" if spec.unit else "")
        if spec.values is not None and value not in spec.values:
            return (f"{value} is not one of {', '.join(str(v) for v in spec.values)}"
                    + (f" {spec.unit}" if spec.unit else ""))
    elif spec.type == "bool":
        if not isinstance(value, bool):
            return f"expected true or false, got {value!r}"
    elif not isinstance(value, str):
        return f"expected a string, got {value!r}"
    elif spec.pattern is not None and not re.fullmatch(spec.pattern, value):
        return f"'{value}' does not match {spec.pattern}"
    return None


def _manifests(modules: ModuleSet) -> tuple[dict[str, Manifest], list[Message]]:
    """Manifests whose parameter schema, constraints and console are consistent; messages for the others."""
    messages: list[Message] = []
    valid: dict[str, Manifest] = {}
    for name, m in modules.manifests.items():
        before = len(messages)

        def error(path, text, name=name):
            messages.append(modules.message("error", name, path, text))

        for pname, spec in m.parameters.items():
            where = ("parameters", pname)
            problems = len(messages)
            if spec.type == "int":
                if spec.min is None or spec.max is None:
                    error(where, f"int parameter {pname} needs min and max")
                elif not (spec.min <= spec.max and (-(1 << 31) <= spec.min and spec.max < (1 << 31)
                                                    or 0 <= spec.min and spec.max < (1 << 32))):
                    error(where, f"range {spec.min}..{spec.max} of {pname} does not fit a 32-bit integer")
            elif spec.min is not None or spec.max is not None:
                error(where, f"min/max only apply to int parameters ({pname} is {spec.type})")
            if spec.pattern is not None:
                if spec.type != "str":
                    error(where + ("pattern",), f"pattern only applies to str parameters ({pname} is {spec.type})")
                else:
                    try:
                        re.compile(spec.pattern)
                    except re.error as e:
                        error(where + ("pattern",), f"invalid regular expression '{spec.pattern}': {e}")
            if spec.names is not None and spec.type != "str":
                error(where + ("names",), f"names only applies to str parameters ({pname} is {spec.type})")
            # IO-35: fixed steps of a setting, and the power stage fact a gate driver parameter sets
            if spec.values is not None:
                if spec.type != "int":
                    error(where + ("values",), f"values only applies to int parameters ({pname} is {spec.type})")
                elif not spec.values or len(set(spec.values)) != len(spec.values):
                    error(where + ("values",), f"values of {pname} must list distinct integers")
                elif spec.min is not None and spec.max is not None:
                    outside = [v for v in spec.values if not spec.min <= v <= spec.max]
                    if outside:
                        error(where + ("values",), f"values {', '.join(map(str, outside))} of {pname} are out of "
                              f"range {spec.min}..{spec.max}")
            if spec.power_stage is not None:
                unit = POWER_STAGE_UNITS[spec.power_stage]
                if "gate_driver" not in m.provides:
                    error(where + ("power_stage",), f"power_stage only applies to modules that provide the role "
                          f"gate_driver ({name} drives no power stage)")
                elif spec.type != "int" or spec.unit != unit:
                    error(where + ("power_stage",), f"{pname} sets {spec.power_stage}: it must be an int parameter "
                          f"in {unit} (is {spec.type}" + (f" in {spec.unit})" if spec.unit else " without unit)"))
            # IO-21: a level of the physical value of an analog connection (sensed.py checks it per project)
            if spec.sense is not None:
                connection = m.connect.get(spec.sense)
                if connection is None or connection.kind != "analog_input":
                    analog = [k for k, c in m.connect.items() if c.kind == "analog_input"]
                    error(where + ("sense",), f"sense: {spec.sense} is no analog_input connection of {name} "
                          f"(analog_input connections: {', '.join(analog) or '-'})")
                elif spec.type != "int" or not spec.unit:
                    error(where + ("sense",), f"{pname} is compared with the physical value of {spec.sense}: it must "
                          f"be an int parameter with unit (is {spec.type}"
                          + (f" in {spec.unit})" if spec.unit else " without unit)"))
            if spec.default is not None and len(messages) == problems:
                problem = _value_problem(spec, spec.default)
                if problem:
                    error(where + ("default",), f"default of {pname}: {problem}")
        if VERIFY_KEY in m.parameters:
            error(("parameters", VERIFY_KEY), f"{VERIFY_KEY} is reserved for every module entry of a project")
        for i, rule in enumerate(m.constraints):
            match = CONSTRAINT_RE.match(rule)
            if match is None:
                error(("constraints", i), f"invalid constraint '{rule}' (expected <operand> <op> <operand>, "
                      f"op one of {' '.join(_COMPARE)})")
                continue
            for operand in (match.group(1), match.group(3)):
                spec = m.parameters.get(operand)
                if not re.fullmatch(_NUMBER, operand) and (spec is None or spec.type != "int"):
                    error(("constraints", i), f"constraint '{rule}': {operand} is no int parameter of {name}")
        if m.console == "instance":
            spec = m.parameters.get(CONSOLE_PARAMETER)
            if spec is None or spec.type != "str":
                error(("console",), f"console: instance needs a str parameter {CONSOLE_PARAMETER}")
        # IO-45: the phases a gate driver drives - only a module that provides that role has them
        if m.phases is not None and "gate_driver" not in m.provides:
            error(("phases",), f"phases only applies to modules that provide the role gate_driver ({name} drives no "
                  f"power stage)")
        if m.project_parameters:
            # A module without a board device: its values stand once under modules.<module> in the project.
            if m.kind == "device_driver":
                error(("project_parameters",), f"{name} is a device driver: its parameters are values of an "
                      "instance (project modules.<device>), not of the module")
            elif not m.parameters:
                error(("project_parameters",), f"{name} has no parameters the project could set")
            elif m.connect:
                error(("project_parameters",), f"{name} has connections, so it needs a board device "
                      "(devices[].connect) and parameters per instance")
        # IO-33: part pins wired per board; a local wake-up input needs the use wake_input and its own number
        wake_numbers: dict[int, str] = {}
        for pin, pspec in m.pins.items():
            if pspec.local_wakeup is None:
                continue
            if WAKE_INPUT_USE not in pspec.uses:
                error(("pins", pin, "local_wakeup"), f"local_wakeup needs the use {WAKE_INPUT_USE} "
                      f"(uses of {pin}: {', '.join(pspec.uses)})")
            elif pspec.local_wakeup in wake_numbers:
                error(("pins", pin, "local_wakeup"), f"local wake-up input {pspec.local_wakeup} is already pin "
                      f"{wake_numbers[pspec.local_wakeup]}")
            else:
                wake_numbers[pspec.local_wakeup] = pin
        _rte_problems(m, error)
        _binding_problems(m, error, modules.files.get(name))
        notes: list[tuple[tuple, str]] = []
        _licence_problems(m, error, lambda path, text: notes.append((path, text)))
        if len(messages) == before:
            valid[name] = m
        messages.extend(modules.message("warning", name, path, text) for path, text in notes)
    _rte_prefix_clashes(modules, valid, messages)
    return valid, messages


def _rank(cls: str) -> int:
    return DISTRIBUTION_CLASSES.index(cls)


def _licence_problems(m: Manifest, error, warning) -> None:
    """PLAT-49: licence, distribution class and provenance of one manifest fit together
    (doc/architecture/lizenz-und-release.md section 5.2). The class of the module is the highest that its sources
    and its licence need; a lower stated class would let NDA or customer material into a lower release line."""
    if m.provenance and m.distribution is None:
        error(("provenance",), "provenance needs distribution: state the release class the sources lead to "
              f"(at least {max((PROVENANCE_ACCESS[p.access] for p in m.provenance), key=_rank)})")
    if m.distribution == "workstation":
        error(("distribution",), "workstation content is never in a repository, a module folder in a tree cannot "
              "be workstation; use private, or keep the files outside the tree")
    if m.distribution is not None:
        for i, p in enumerate(m.provenance):
            need = PROVENANCE_ACCESS[p.access]
            if _rank(m.distribution) < _rank(need):
                error(("distribution",), f"distribution {m.distribution} is lower than provenance[{i}] needs "
                      f"('{p.doc}', access {p.access}: at least {need})")
    restricted = m.distribution in ("licensed", "nda")
    if m.license is not None:
        idents = spdx_identifiers(m.license)
        if restricted and not any(i in LICENSE_REF_CLASSES for i in idents):
            error(("license",), f"distribution {m.distribution} needs a licence of this tree that restricts passing "
                  f"on ({', '.join(LICENSE_REF_CLASSES)}); {m.license} would let every recipient pass the files on")
        for ident in idents:
            need = LICENSE_REF_CLASSES.get(ident)
            if need is None and ident.startswith("LicenseRef-"):
                error(("license",), f"unknown licence {ident}: the LicenseRef identifiers of this tree are "
                      f"{', '.join(LICENSE_REF_CLASSES)} (texts under LICENSES/)")
            elif need is not None and (m.distribution is None or _rank(m.distribution) < _rank(need)):
                error(("license",), f"licence {ident} needs distribution {need} or higher "
                      f"(is {m.distribution or 'not stated'}): it must never reach a lower release line")
    elif restricted:
        error(("distribution",), f"distribution {m.distribution} needs license: without it the module's files "
              f"would fall back to the licence of the tree (Apache-2.0)")
    for i, p in enumerate(m.provenance):
        if p.verify:
            warning(("provenance", i, "verify"), f"access of '{p.doc}' is not established yet ({p.access}); the "
                    f"distribution class {m.distribution} rests on it")


# ARCH-52: lifecycle levels at which the application hands the platform its components (ecu/application app.cpp
# calls platformLifecycleAdd for these); a bound module registers its instances at its declared level.
PLATFORM_LIFECYCLE_LEVELS = (1, 2)
# ARCH-52: contracts the family binders provide to a bound module (one object per instance and contract)
BINDING_CONTRACTS = {"spi_device": 1}
# ARCH-52: how the generated binding reads an instance for the RTE: only the generic reader contract; the access
# codes of tools/rtegen (gate_driver, hall, input_capture) belong to hand-written integrations
BINDING_RTE_ACCESS = ("reading",)


def _binding_problems(m: Manifest, error, manifest_file: Path | None) -> None:
    """ARCH-52: a module with `binding` can be integrated by the generator (tools/bswgen render_binding_header)."""
    if m.binding is None:
        return
    if m.kind != "device_driver":
        error(("binding",), f"binding needs a device driver ({m.module} is {m.kind}): the generated integration "
              f"creates one object per board device")
        return
    if m.lifecycle is None:
        error(("binding",), f"binding needs lifecycle (level and task): the generated integration registers every "
              f"instance of {m.module} at that level in that task")
    elif m.lifecycle.level not in PLATFORM_LIFECYCLE_LEVELS:
        error(("lifecycle", "level"), f"lifecycle level {m.lifecycle.level} is not offered to the platform: the "
              f"application adds platform components at level {' and '.join(map(str, PLATFORM_LIFECYCLE_LEVELS))} "
              f"(platformLifecycleAdd)")
    for contract, count in m.requires.items():
        if BINDING_CONTRACTS.get(contract) != count:
            offered = ", ".join(f"{k}: {v}" for k, v in BINDING_CONTRACTS.items())
            error(("requires", contract), f"the family binders offer a bound module {offered}; {contract}: {count} "
                  f"cannot be bound by the generator")
    if m.rte is not None and m.rte.access not in BINDING_RTE_ACCESS:
        error(("rte", "access"), f"rte access {m.rte.access} is access code of tools/rtegen for a hand-written "
              f"integration; a bound module uses access reading (the trait's rte() fills the reading)")
    if manifest_file is not None:
        header = Path(manifest_file).parent / "include" / m.binding.include
        if not header.is_file():
            error(("binding", "include"), f"header {m.binding.include} not found in the include directory of "
                  f"{m.module} ({header.parent.parent.name}/include)")


def _rte_problems(m: Manifest, error) -> None:
    """RTE-18: the RTE values of one manifest (module.yaml rte) fit its kind; the prefix is free."""
    if m.rte is None:
        return
    prefix = m.rte_prefix
    if prefix in RTE_RESERVED_PREFIXES:
        error(("rte", "prefix"), f"RTE prefix {prefix} is a target kind of tools/rtegen already "
              f"({', '.join(RTE_RESERVED_PREFIXES)}); choose another rte.prefix")
    elif not re.fullmatch(r"[a-z][a-z0-9_]*", prefix):
        error(("rte",), f"module name {m.module} gives no RTE prefix ({prefix}); set rte.prefix "
              f"(lower case letters, digits, _)")
    if m.rte.instances == "devices" and m.kind != "device_driver":
        error(("rte", "instances"), f"instances devices needs a device driver ({m.module} is {m.kind}): only a "
              f"device driver has board devices")
    if m.rte.instances == "input_capture" and m.kind != "mcu_driver":
        error(("rte", "instances"), f"instances input_capture needs an mcu_driver ({m.module} is {m.kind})")


def _rte_prefix_clashes(modules: ModuleSet, valid: dict[str, Manifest], messages: list[Message]) -> None:
    """RTE-18: two modules must not give the same RTE prefix, and one access code of tools/rtegen serves one module
    (its code reads one set of instances); the later manifest is reported and not used."""
    prefixes: dict[str, str] = {}
    access: dict[str, str] = {}
    for name in list(valid):
        m = valid[name]
        if m.rte is None:
            continue
        clash = None
        if m.rte_prefix in prefixes:
            clash = (("rte", "prefix"), f"RTE prefix {m.rte_prefix} is also the one of module "
                                        f"{prefixes[m.rte_prefix]}")
        elif m.rte.access != "reading" and m.rte.access in access:
            clash = (("rte", "access"), f"access {m.rte.access} is already used by module {access[m.rte.access]} "
                                        f"(one module per access code of tools/rtegen; a new module uses reading)")
        if clash is not None:
            messages.append(modules.message("error", name, *clash))
            del valid[name]
            continue
        prefixes[m.rte_prefix] = name
        if m.rte.access != "reading":
            access[m.rte.access] = name


# ParameterSpec.names -> what a name is and where the project lists them (None: any name the pattern allows)
NAME_SPACES = {"can_bus": ("CAN bus", "use.can"), "any": ("name", None)}


def _name_problem(spec: ParameterSpec, value: str, names: dict[str, list[str]]) -> str | None:
    """Why a parameter that is a list of names (ParameterSpec.names) names a wrong one, None if it does not."""
    if spec.names is None or value in ("all", "none"):
        return None
    what, where = NAME_SPACES[spec.names]
    chosen = value.split(",")
    if where is not None:
        known = names.get(spec.names, [])
        for entry in chosen:
            if entry not in known:
                return f"{entry} is no {what} of the project ({where}: {', '.join(known) or 'none'})"
    if len(set(chosen)) != len(chosen):
        return f"a {what} is named twice ({value})"
    return None


def _parameters(rep: Reporter, key: str, manifest: Manifest, given: dict,
                names: dict[str, list[str]] | None = None) -> dict | None:
    """Project values of one module instance checked against the parameter schema, defaults filled in.

    `names`: entries of the project a parameter with ParameterSpec.names may refer to, by name space.
    """
    values: dict = {}
    ok = True
    for pname, value in given.items():
        path = ("modules", key, pname)
        if pname == VERIFY_KEY:
            if not isinstance(value, bool):
                rep.error("project", path, f"{VERIFY_KEY} must be true or false")
                ok = False
            continue
        spec = manifest.parameters.get(pname)
        if spec is None:
            rep.error("project", path, f"module {manifest.module} has no parameter {pname} "
                      f"(parameters: {', '.join(manifest.parameters) or '-'})")
            ok = False
            continue
        problem = _value_problem(spec, value) or _name_problem(spec, value, names or {})
        if problem:
            rep.error("project", path, f"{pname}: {problem}")
            ok = False
            continue
        values[pname] = value
    for pname, spec in manifest.parameters.items():
        if pname in given:
            continue
        if spec.default is None:
            rep.error("project", ("modules", key), f"missing parameter {pname} of module {manifest.module}"
                      + (f" ({spec.description})" if spec.description else ""))
            ok = False
        else:
            values[pname] = spec.default
    if not ok:
        return None
    for rule in manifest.constraints:
        left, op, right = CONSTRAINT_RE.match(rule).groups()
        lv, rv = (int(x) if re.fullmatch(_NUMBER, x) else values[x] for x in (left, right))
        if not _COMPARE[op](lv, rv):
            named = [x for x in (left, right) if x in values]
            path = next((("modules", key, x) for x in named if x in given), ("modules", key))
            rep.error("project", path, f"constraint of module {manifest.module} not met: {rule} "
                      f"({', '.join(f'{x} {values[x]}' for x in named)})")
            ok = False
    return values if ok else None


def _entry_kind(cfg: Config, name: str) -> str | None:
    """Connect kind of the board entry called `name`, its board section for other entries, None if unknown."""
    for kind, section in CONNECT_KINDS.items():
        entries = getattr(cfg.board.dio, section[4:]) if section.startswith("dio.") else getattr(cfg.board, section)
        if any(e.name == name for e in entries):
            return kind
    for section in BUS_KINDS + ("input_capture",):
        if any(e.name == name for e in getattr(cfg.board, section)):
            return section
    if any(ch.name == name for grp in cfg.board.pwm for ch in grp.channels):
        return "pwm channel"
    return None


# ARCH-44: roles a project has at most one provider of, read from module.yaml `provides` (never from part names).
# power_supply is the device that manages the supply of the ECU (V1/V2, Sleep, wake-up, start reasons): ecuState,
# the RTE services bsw.power/bsw.reset and the SBC port (SbcSystem, bsp/sbc/sbcConfiguration.h "more than one UJA113x")
# serve exactly one. BswGenSbcConfigurationProbeTest (tools/bswgen/test_bswgen.py) runs this rule and the #error
# rule of the port header against the same profiles.
SINGLE_PROVIDER_ROLES = ("power_supply",)


def _use_path(cfg: Config, dev: DeviceUse, bus: str | None = None) -> tuple:
    """YAML path of the project entry that brings `dev` (or its bus `bus`) into the build: the bus in use.spi or
    use.can (the whole selection if it is 'all'), modules.<device> for a device wired through connect only."""
    for kind, buses in (("spi", dev.spi), ("can", dev.can_phy)):
        if buses and (bus is None or bus in buses):
            name = buses[0] if bus is None else bus
            selection = getattr(cfg.project.use, kind)
            return ("use", kind) if selection == "all" else ("use", kind, list(selection).index(name))
    return ("modules", dev.name)


# COM-21 (COM-07): modules that take modules.<device>.can_wakeup_frame, and the bit rates their partial networking
# can decode (UJA113x data sheet Rev. 2, table 29, CDR; the driver's dataRateCode() refuses every other rate).
CAN_WAKEUP_FRAME_MODULES = ("sbcUja113x",)
CAN_WAKEUP_BITRATES = (50000, 100000, 125000, 250000, 500000, 1000000)


def _can_wakeup_frame(cfg: Config, rep: Reporter, device: str, part: str, frame: CanWakeupFrame,
                      can_used: list[str], module_uses: dict) -> "CanWakeupFrameUse | None":
    """Checks modules.<device>.can_wakeup_frame (the rules of sbc::Uja113x::isValidCanWakeupFrame plus the
    project context) and derives the bit rate from can.<bus>.bitrate; None after an error."""
    path = ("modules", device, CAN_WAKEUP_FRAME)
    errors: list[tuple[tuple, str]] = []
    if "/FD" not in part.upper():
        errors.append((path, f"part {part} of device {device} has no partial networking (only the /FD variants of "
                             "the UJA113x filter a wake-up frame); leave can_wakeup_frame out"))
    bus = can_used[0] if can_used else None
    bitrate = None
    if bus is None:
        errors.append((path, f"device {device} is the transceiver of no CAN bus the project uses (use.can)"))
    else:
        timing = cfg.project.can.get(bus)
        bitrate = timing.bitrate if timing is not None else None
        if bitrate is not None and bitrate not in CAN_WAKEUP_BITRATES:
            errors.append((path, f"CAN bus {bus} runs at {bitrate} bit/s (can.{bus}.bitrate), partial networking "
                                 f"decodes only {', '.join(str(b) for b in CAN_WAKEUP_BITRATES)} bit/s"))
    bits = 29 if frame.extended else 11
    width = (1 << bits) - 1
    if frame.id > width:
        errors.append((path + ("id",), f"id 0x{frame.id:X} is wider than {bits} bit"
                                       + ("" if frame.extended else " (extended: true for a 29 bit identifier)")))
    mask = width if frame.compare_mask is None else frame.compare_mask
    if mask > width:
        errors.append((path + ("compare_mask",), f"compare_mask 0x{mask:X} is wider than {bits} bit"))
    if frame.dlc is None:
        if frame.data_mask:
            errors.append((path + ("data_mask",), "data_mask needs dlc (the data field is only compared with a "
                                                  "data length)"))
    elif len(frame.data_mask) != frame.dlc:
        errors.append((path + ("data_mask",), f"data_mask has {len(frame.data_mask)} entries, dlc is {frame.dlc}"))
    elif frame.dlc > 0 and not any(frame.data_mask):
        errors.append((path + ("data_mask",), "data_mask is 0 in every byte: the SBC wakes only on a data bit set "
                                              "in both frame and mask, this frame would never wake the ECU"))
    for where, text in errors:
        rep.error("project", where, text)
    if errors or bitrate is None:           # no can.<bus>: reported by the bit timing check
        return None
    ecu = module_uses.get("ecuState")
    wakeup_can = ecu.parameters.get("wakeup_can") if ecu is not None else None
    if isinstance(wakeup_can, str) and wakeup_can != "all" and bus not in wakeup_can.split(","):
        rep.warning("project", path, f"CAN bus {bus} is not in modules.ecuState.wakeup_can ({wakeup_can}): the "
                                     "wake-up frame never wakes the ECU")
    return CanWakeupFrameUse(bus, bitrate, frame.id, frame.extended, mask, frame.dlc, list(frame.data_mask),
                             frame.fd_passive)


def device_counts(cfg: Config, rep: Reporter, manifests: dict[str, Manifest], devices: list[DeviceUse]):
    """Counts of the used devices against their modules (ARCH-44); findings on the project entry that adds the
    surplus device or bus.

    - A role of SINGLE_PROVIDER_ROLES has at most one used provider.
    - `requires: {spi_device: n}`: the device is reached over exactly n used SPI buses. The driver gets one
      ISpiDevice per contract; a second used bus of the same device would be pinned and clocked but served by
      nobody (sbcConfiguration.h, Integration.cpp: "needs exactly one SPI bus"). No used bus at all is reported by
      integrate() already (board device without bus, or transceiver used without its SPI bus).
    """
    for role in SINGLE_PROVIDER_ROLES:
        providers = [d for d in devices if role in manifests[d.module].provides]
        for extra in providers[1:]:
            first = providers[0]
            rep.error("project", _use_path(cfg, extra),
                      f"device {extra.name} ({extra.module}) is a second provider of the role {role}: the project "
                      f"already uses {first.name} ({first.module}); a project has one provider of {role} (one SBC), "
                      f"use only one of them")
    for dev in devices:
        need = manifests[dev.module].requires.get("spi_device")
        if not need or len(dev.spi) == need:
            continue
        surplus = dev.spi[need] if len(dev.spi) > need else None
        rep.error("project", _use_path(cfg, dev, surplus),
                  f"device {dev.name} ({dev.module}) needs exactly {need} used SPI bus{'es' if need > 1 else ''} "
                  f"(module requires spi_device: {need}), the project uses {len(dev.spi)}: {', '.join(dev.spi)} "
                  f"(use.spi)")


def instance_limits(cfg: Config, rep: Reporter, manifests: dict[str, Manifest], devices: list[DeviceUse]):
    """ARCH-52: `max_instances` of a module; every used device beyond it is reported at the project entry that brings
    it into the build (the bus in use, or modules.<device>)."""
    for name, manifest in manifests.items():
        if manifest.max_instances is None:
            continue
        used = [d for d in devices if d.module == name]
        for extra in used[manifest.max_instances:]:
            rep.error("project", _use_path(cfg, extra),
                      f"device {extra.name} ({name}) is instance {used.index(extra) + 1} of module {name}, which "
                      f"allows at most {manifest.max_instances} per project (module.yaml max_instances; already "
                      f"used: {', '.join(d.name for d in used[:manifest.max_instances])})")


def _binding_tasks(cfg: Config, rep: Reporter, modules: ModuleSet, manifests: dict[str, Manifest],
                   devices: list[DeviceUse], manifest: Path | None = None):
    """ARCH-52: the generated binding creates every instance of a bound module in the task of its lifecycle entry
    (TASK_<TASK> of async/Config.h); that task must be a BSW task of the task manifest (or the idle task). Reported at
    the manifest of the module, once, and only for a module the project uses. A task manifest that cannot be read is
    reported by tools/rtegen."""
    used = {d.module for d in devices}
    bound = [n for n in sorted(used) if n in manifests and manifests[n].binding is not None
             and manifests[n].lifecycle is not None]
    if not bound:
        return
    keys = bsw_task_keys(tasks_manifest(cfg.running_tree) if manifest is None else manifest)
    if not keys.bsw:
        return
    known = [*keys.bsw, "idle"]
    for name in bound:
        task = manifests[name].lifecycle.task
        if task not in known:
            rep.messages.append(modules.message(
                "error", name, ("lifecycle", "task"),
                f"task {task} of module {name} is no BSW task ({', '.join(known)}; ecu/asyncCoreConfiguration/"
                f"tasks.yaml): the generated integration creates its instances in TASK_{task.upper()}"))


def _gate_driver_groups(cfg: Config, rep: Reporter, manifests: dict[str, Manifest], devices: list[DeviceUse]):
    """IO-45: the binding of a gate driver to its PWM group (board pwm[].gate_driver, IO-25) is what makes the driver's
    enable rule effective - the family port attaches a phase duty source that reads the timer of that group - and
    what puts its enable into the safe-output group of that PWM group (degraded.py). Warnings, with the YAML path of
    the value to change, for an integrated gate driver whose module states its phases (module.yaml `phases`):
    - the board binds it to no PWM group (board devices[<i>]);
    - the group has another number of pairs than the device has phases (board pwm[<g>].pairs): the rule compares the
      phases of one bridge, a missing pair would be a phase whose state nobody knows.
    In both cases the source is not attached and the driver keeps the behaviour it had before IO-44. A project that
    does not use the bound group has no power stage to drive (ARCH-17, e.g. the timer serves an input capture) and gets
    no warning; its driver has no source either."""
    board = cfg.board
    used = {grp.name for grp, _ in cfg.selected_pwm()}
    for dev in devices:
        phases = manifests[dev.module].phases
        if phases is None:
            continue
        rule = f"the enable rule of {dev.name} ({dev.module}: all {phases} phases in one state) is not applied"
        loose = ", and its enable belongs to no safe-output group (it goes off with S1 only)"
        index = next((g for g, grp in enumerate(board.pwm) if grp.gate_driver == dev.name), None)
        if index is None:
            where = next(i for i, d in enumerate(board.devices) if d.name == dev.name)
            rep.warning("board", ("devices", where), f"gate driver {dev.name} is bound to no PWM group (board "
                        f"pwm[].gate_driver): {rule}{loose}")
            continue
        grp = board.pwm[index]
        if grp.name not in used:
            continue  # a project without the power stage (e.g. its timer serves an input capture, ARCH-17)
        if len(grp.pairs) != phases:
            rep.warning("board", ("pwm", index, "pairs"), f"PWM group {grp.name} has {len(grp.pairs)} pair"
                        f"{'s' if len(grp.pairs) != 1 else ''}, its gate driver {dev.name} ({dev.module}) drives "
                        f"{phases} phases: {rule}")


def _device_pins(rep: Reporter, board, device_module: dict[str, str], manifests: dict[str, Manifest],
                 known_modules: dict[str, Manifest]) -> dict[str, dict[str, str]]:
    """IO-33: board devices[].pins against the pin list of the device's module (module.yaml `pins`).

    A pin must be one the module lists and the part has (PinSpec.parts), its use one the module serves for it.
    Returns the valid wiring per device (pin -> use); pins with a finding are left out, so nothing is generated
    for them.
    """
    result: dict[str, dict[str, str]] = {}
    for i, dev in enumerate(board.devices):
        if not dev.pins:
            continue
        where = ("devices", i, "pins")
        module = device_module.get(dev.name)
        if module is None:
            # a module named, matched twice or with a faulty manifest is reported at the board/manifest already
            if dev.module is None and not any(fnmatchcase(dev.part, p) for m in known_modules.values()
                                              for p in m.parts):
                rep.error("board", where, f"device {dev.name} has no module (no module.yaml lists part {dev.part}), "
                          "pins needs one")
            continue
        spec = manifests[module].pins
        wiring: dict[str, str] = {}
        for pin, fact in dev.pins.items():
            known = spec.get(pin)
            if known is None:
                rep.error("board", where + (pin,), f"module {module} has no pin {pin} "
                          f"(pins: {', '.join(spec) or '-'})")
            elif known.parts and not any(fnmatchcase(dev.part, p) for p in known.parts):
                rep.error("board", where + (pin,), f"part {dev.part} of device {dev.name} has no pin {pin} "
                          f"(only {', '.join(known.parts)})")
            elif fact.use not in known.uses:
                rep.error("board", where + (pin, "use"), f"module {module} has no use {fact.use} for pin {pin} "
                          f"(uses: {', '.join(known.uses)})")
            else:
                wiring[pin] = fact.use
        result[dev.name] = wiring
    return result


def integrate(cfg: Config, rep: Reporter, modules: ModuleSet) -> Integration:
    """Module instances and CAN transceiver roles of a project; findings with YAML path go to `rep`.

    Devices with buses (spi[].device, can[].phy) are used if the project uses one of their buses; devices wired
    only through connect if the project has an entry modules.<device> (their parameters). A module without a board
    device that declares project_parameters is always part of the build; modules.<module> only overrides its
    defaults. Family independent.
    """
    start = len(rep.messages)
    rep.messages.extend(modules.messages)
    valid, manifest_messages = _manifests(modules)
    rep.messages.extend(manifest_messages)
    board = cfg.board
    used_spi = {b.name for b in cfg.selected("spi")}
    used_can = {b.name for b in cfg.selected("can")}
    # entries of the project a parameter may name (ParameterSpec.names), in the order of the project
    names = {"can_bus": [b.name for b in cfg.selected("can")]}
    drivers = sorted(n for n, m in modules.manifests.items() if m.kind == "device_driver")

    # --- board: module of every device
    device_module: dict[str, str] = {}
    for i, dev in enumerate(board.devices):
        if dev.module is not None:
            m = modules.manifests.get(dev.module)
            if m is None:
                rep.error("board", ("devices", i, "module"), f"unknown module {dev.module} "
                          f"(device drivers: {', '.join(drivers) or '-'})")
            elif m.kind != "device_driver":
                rep.error("board", ("devices", i, "module"), f"module {dev.module} is a {m.kind}, not a device driver")
            elif not any(fnmatchcase(dev.part, p) for p in m.parts):
                rep.error("board", ("devices", i, "part"), f"part {dev.part} of device {dev.name} is not served by "
                          f"module {dev.module} (parts: {', '.join(m.parts) or '-'})")
            elif dev.module in valid:
                device_module[dev.name] = dev.module
            continue
        matches = sorted(name for name, m in modules.manifests.items()
                         if any(fnmatchcase(dev.part, pattern) for pattern in m.parts))
        if len(matches) > 1:
            rep.error("board", ("devices", i, "part"), f"part {dev.part} of device {dev.name} is served by several "
                      f"modules: {', '.join(matches)} (choose one with module:)")
        elif matches and matches[0] in valid:
            device_module[dev.name] = matches[0]
        elif not matches and dev.connect:
            rep.error("board", ("devices", i, "connect"), f"device {dev.name} has no module (no module.yaml lists "
                      f"part {dev.part}), connect needs one")

    # --- board: connections against the module's connect contract (all devices, used or not)
    connected: dict[str, str] = {}
    wired: set[str] = set()                     # devices whose connections are complete and valid
    for i, dev in enumerate(board.devices):
        module = device_module.get(dev.name)
        if module is None:
            continue
        before = len(rep.messages)
        spec = valid[module].connect
        where = ("devices", i, "connect")
        for key, target in dev.connect.items():
            if key not in spec:
                rep.error("board", where + (key,), f"module {module} has no connection {key} "
                          f"(connections: {', '.join(spec) or '-'})")
                continue
            kind = _entry_kind(cfg, target)
            if kind != spec[key].kind:
                rep.error("board", where + (key,), f"connection {key} of module {module} needs a {spec[key].kind} "
                          f"(board {CONNECT_KINDS[spec[key].kind]}), {target} is "
                          + (f"a {kind}" if kind else "no board entry"))
            elif target in connected:
                rep.error("board", where + (key,), f"{target} is already connected to {connected[target]}")
            else:
                connected[target] = f"{dev.name} ({key})"
        missing = [k for k in spec if k not in dev.connect and not spec[k].optional]  # ARCH-52: optional may stay open
        if missing:
            rep.error("board", where, f"device {dev.name} ({module}) needs connect: {', '.join(missing)}")
        if len(rep.messages) == before:
            wired.add(dev.name)

    # --- board: wiring of the part's own pins against the module's pin list (IO-33)
    pinned = _device_pins(rep, board, device_module, valid, modules.manifests)

    # --- board: a PWM group names a gate driver whose module provides that role (IO-25)
    for g, grp in enumerate(board.pwm):
        module = device_module.get(grp.gate_driver) if grp.gate_driver else None
        if module is not None and "gate_driver" not in valid[module].provides:
            rep.error("board", ("pwm", g, "gate_driver"), f"device {grp.gate_driver} ({module}) is no gate driver: "
                      f"module {module} does not provide the role gate_driver")

    # --- project: module entries refer to devices with a module, or to a module with project_parameters
    params = cfg.project.modules
    configurable = [n for n, m in modules.manifests.items() if m.project_parameters]
    for key in params:
        index = next((i for i, d in enumerate(board.devices) if d.name == key), None)
        if index is not None:
            if key not in device_module:
                dev = board.devices[index]
                # a module named on the board or matching the part, but unusable, is reported at the board/manifest
                if dev.module is None and not any(fnmatchcase(dev.part, p) for m in modules.manifests.values()
                                                  for p in m.parts):
                    rep.error("project", ("modules", key), f"device {key} ({dev.part}) has no module")
            continue
        manifest = modules.manifests.get(key)
        if manifest is not None and manifest.project_parameters:
            continue                                # parameters are checked below, with the defaults filled in
        with_module = [d.name for d in board.devices if d.name in device_module]
        text = (f"board {board.board.name} has no device {key} "
                f"(devices with a module: {', '.join(with_module) or '-'})")
        if manifest is not None:
            text = (f"module {key} ({manifest.kind}) takes no parameters from the project; "
                    f"modules that do: {', '.join(sorted(configurable)) or '-'}")
        elif configurable:
            text += f" and no module {key} (modules with parameters: {', '.join(sorted(configurable))})"
        rep.error("project", ("modules", key), text)

    # --- modules without a board device: values of the project plus the defaults of the manifest. Every such
    # module is part of the build, so it is checked (and generated) whether the project names it or not.
    module_uses: dict[str, ModuleUse] = {}
    for name in configurable:
        if name not in valid:
            continue                                # faulty manifest: reported at the manifest
        given = params.get(name, {})
        values = _parameters(rep, name, valid[name], given, names)
        if values is not None:
            module_uses[name] = ModuleUse(name, values, given.get(VERIFY_KEY) is True)

    # --- used devices
    devices: list[DeviceUse] = []
    consoles: dict[str, str] = {m.console: f"module {n}" for n, m in modules.manifests.items()
                                if m.console and m.console != "instance"}
    for i, dev in enumerate(board.devices):
        module = device_module.get(dev.name)
        if module is None:
            continue
        manifest = valid[module]
        spi_refs = [b.name for b in board.spi if b.device == dev.name]
        can_refs = [b.name for b in board.can if b.phy == dev.name]
        if manifest.requires.get("spi_device") and not spi_refs:
            rep.error("board", ("devices", i), f"device {dev.name} ({module}) needs an SPI bus with device: "
                      f"{dev.name} (module requires spi_device)")
            continue
        if can_refs and "can_phy" not in manifest.provides:
            j = next(k for k, b in enumerate(board.can) if b.phy == dev.name)
            rep.error("board", ("can", j, "phy"), f"device {dev.name} is the transceiver of CAN bus {can_refs[0]}, "
                      f"but module {module} does not provide the role can_phy")
            continue
        spi_used = [n for n in spi_refs if n in used_spi]
        can_used = [n for n in can_refs if n in used_can]
        if spi_refs or can_refs:
            # A bus-connected device is part of the project if the project uses one of its buses.
            if not (spi_used or can_used):
                if dev.name in params:
                    rep.error("project", ("modules", dev.name), f"device {dev.name} is not used: the project uses "
                              f"none of its buses ({', '.join(spi_refs + can_refs)})")
                continue
            if manifest.requires.get("spi_device") and not spi_used:
                # Transceiver used, control bus not: the driver could not run. spi_refs is not empty here (a device
                # without SPI bus is reported above); ARCH-44: no bare next() on the board list all the same.
                j = next((k for k, b in enumerate(board.spi) if b.device == dev.name), None)
                rep.error("board", ("spi", j, "device") if j is not None else ("devices", i),
                          f"CAN bus {can_used[0]} uses device {dev.name} as transceiver, but the project does not "
                          f"use its SPI bus {', '.join(spi_refs)} (use.spi)")
                continue
        elif dev.name not in params:
            continue                                # wired through connect only: used with modules.<device>
        given = params.get(dev.name, {})
        frame = given.get(CAN_WAKEUP_FRAME) if module in CAN_WAKEUP_FRAME_MODULES else None
        if frame is not None:
            given = {k: v for k, v in given.items() if k != CAN_WAKEUP_FRAME}
        values = _parameters(rep, dev.name, manifest, given, names)
        # an invalid frame is an error of the project; the device stays, so no follow-up findings (CAN bus roles)
        wakeup_frame = (_can_wakeup_frame(cfg, rep, dev.name, dev.part, frame, can_used, module_uses)
                        if frame is not None else None)
        if values is None or dev.name not in wired:
            continue
        usable = True
        connect = dict(dev.connect)
        for key, target in dev.connect.items():
            section = CONNECT_KINDS[manifest.connect[key].kind]
            if target not in {e.name for e in cfg.selected(section)} and manifest.connect[key].optional:
                # IO-68: an optional connection the project does not use is treated as not wired - the instance
                # runs without it (e.g. the SBC without INTN1 polls), instead of losing the whole device
                rep.warning("project", ("modules", dev.name) if dev.name in params else ("use",),
                            f"device {dev.name} ({module}) is connected to {target} ({key}), but the project does "
                            f"not use it (use.{section.split('.')[0]}): the optional connection {key} is treated "
                            "as not wired")
                del connect[key]
            elif target not in {e.name for e in cfg.selected(section)}:
                # Warning, not error: a project copy that narrows use: stays loadable; without the signal the
                # instance cannot run, so it is left out (its pins stay in their reset state).
                rep.warning("project", ("modules", dev.name) if dev.name in params else ("use",),
                            f"device {dev.name} ({module}) is connected to {target} ({key}), but the project does "
                            f"not use it (use.{section.split('.')[0]}): {dev.name} is not instantiated")
                usable = False
        if manifest.console == "instance":
            name = values[CONSOLE_PARAMETER]
            if name in consoles:
                rep.error("project", ("modules", dev.name, CONSOLE_PARAMETER), f"console {name} is already used by "
                          f"{consoles[name]}")
                usable = False
            consoles.setdefault(name, f"device {dev.name}")
        if usable:
            group = next((grp for grp, _ in cfg.selected_pwm() if grp.gate_driver == dev.name), None)
            devices.append(DeviceUse(dev.name, dev.part, module, spi_used, can_used, connect, values,
                                     given.get(VERIFY_KEY) is True, group.name if group else None,
                                     group.mode if group else None,
                                     [(b.name, b.instance, b.pcs.index) for b in board.spi if b.name in spi_used],
                                     wakeup_frame, dict(pinned.get(dev.name, {}))))

    # --- used devices: one provider per single role, the number of SPI buses the module requires (ARCH-44)
    device_counts(cfg, rep, valid, devices)
    # --- ARCH-52: most instances per module, the task of the generated binding
    instance_limits(cfg, rep, valid, devices)
    _binding_tasks(cfg, rep, modules, valid, devices)

    # --- IO-45: a gate driver with a phase count needs its PWM group - one pair per phase - for its enable rule
    _gate_driver_groups(cfg, rep, valid, devices)

    # --- CAN buses: role can_phy
    serving = {d.name: d.module for d in devices}
    can: list[CanBusUse] = []
    for index, bus in enumerate(cfg.selected("can")):
        j = next(k for k, b in enumerate(board.can) if b.name == bus.name)
        module = serving.get(bus.phy) if bus.phy else None
        if module is None:
            part = next((d.part for d in board.devices if d.name == bus.phy), None)
            if bus.phy is None:
                rep.warning("board", ("can", j, "phy"), f"CAN bus {bus.name} describes no transceiver (phy), "
                            "treated as passive (always ready)")
            elif part is not None and not any(fnmatchcase(part, p) for m in modules.manifests.values()
                                              for p in m.parts) and bus.phy not in device_module:
                rep.warning("board", ("can", j, "phy"), f"no module for part {part} of transceiver {bus.phy} "
                            f"(CAN bus {bus.name}), treated as passive transceiver (always ready)")
            module = PASSIVE
        can.append(CanBusUse(bus.name, bus.instance, index, bus.phy, module))

    dio = [ch.name for ch in cfg.selected("dio.inputs")] + [ch.name for ch in cfg.selected("dio.outputs")]
    return Integration(devices, can, [b.name for b in cfg.selected("spi")], dio,
                       [ch.name for ch in cfg.selected("adc")], rep.messages[start:], module_uses,
                       i2c=[b.name for b in cfg.selected("i2c")])


# ----------------------------------------------------------------------------------------------- name lengths
# ARCH-43 (after SYS-16): names from the configuration end up as "%s" arguments or macro strings of log lines. The
# lines are sized for a longest name, and the code asserts every generated name against that limit at compile
# time (static_assert in CanSystem.cpp and Integration.cpp, proofs in tools/logcheck/bounds.yaml). This check
# reports the same names with file, line and YAML path before the build stops there.
#
# One source for every limit: the constant in the C++ code, read here (read_constant). The limit is no value of a
# configuration level (doc/architecture/README.md section 4: derivative, board, project) but a property of the
# code that sizes its log lines - the host tests (CanFrameLogHostTest) and tools/logcheck prove the lines against
# the same constant. A family port without such code (no definition under its root) has no limit to check.


@dataclass(frozen=True)
class NameLimit:
    """Longest name of one kind, defined as integer constant in the C++ code."""

    what: str                   # kind of name, for the messages
    constant: str               # constant as the messages show it (scope::NAME); the definition is found by NAME
    root: str                   # directory relative to the repository ({family}: family of the derivative) that
    #                             holds exactly one definition of the constant in its .h/.hpp/.cpp files
    required: bool = True       # False: a family port without the definition has no such names in its log lines


NAME_LIMITS = {
    # bus names of NX_CAN_BUSES, NX_CAN_MB_VECTORS, NX_BUSID_CAN_BUSES, NX_UDS_BUS_NAME, NX_XCP_BUS_NAME (COM-25)
    "can_bus": NameLimit("CAN bus name", "CanFrameLog::BUS_NAME_CHARS", "libs/bsw/canSystem/include"),
    # controller name of a CAN bus (device name of NX_CAN_BUSES, e.g. "FlexCAN0"), CAN system of the family port
    "can_controller": NameLimit("CAN controller name", "CAN_DEVICE_NAME_MAX_CHARS", "platforms/{family}/port/main",
                                required=False),
    # label of a high-side switch: its console group in upper case (NX_DEVICE_<device>_LABEL, tools/bswgen)
    "switch_label": NameLimit("high-side switch label", "HighSideSwitchNames::LABEL_MAX_CHARS",
                              "libs/bsw/highSideSwitch/include"),
    # label of a TMC6200 gate driver: its device name (#device in GATE_DRIVER_DATA of the family port)
    "gate_driver_label": NameLimit("gate driver label", "GATE_DRIVER_LABEL_MAX_CHARS",
                                   "platforms/{family}/port/main", required=False),
}

# Module instances whose names become log labels: module -> (kind in NAME_LIMITS, source of the label);
# source "console": the console parameter in upper case, "device": the board device name
LABELED_MODULES = {"highSideSwitch": ("switch_label", "console"), "gdTmc6200": ("gate_driver_label", "device")}

CPP_SUFFIXES = (".h", ".hpp", ".cpp")
_CPP_TOKEN_RE = re.compile(r'"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'|//[^\n]*|/\*.*?\*/', re.S)
_INTEGER_RE = re.compile(r"(0[xX][0-9a-fA-F]+|[0-9]+)[uUlL]*")
_constant_cache: dict[tuple[Path, int, str], list[tuple[int, str]]] = {}


def blank_cpp_text(text: str) -> str:
    """C++ source with comments and the contents of string and character literals blanked (quotes stay), so that
    neither can hold a definition; line breaks stay, so line numbers do not move."""
    def blank(match: re.Match) -> str:
        token = match.group(0)
        if token.startswith(("//", "/*")):
            return re.sub(r"[^\n]", " ", token)
        return token[0] + " " * (len(token) - 2) + token[-1]
    return _CPP_TOKEN_RE.sub(blank, text)


def constant_definitions(text: str, name: str) -> list[tuple[int, str]]:
    """(line, initializer) of every constexpr integer definition of `name` in the C++ source `text`."""
    text = blank_cpp_text(text)
    pattern = re.compile(
        r"\b(?:(?:static|inline)\s+)*constexpr\s+(?:(?:static|inline|const)\s+)*"
        r"(?:std::)?(?:size_t|u?int(?:8|16|32|64)_t|unsigned(?:\s+int)?|int)\s+(?:const\s+)?"
        + re.escape(name) + r"\s*(?:=\s*([^;{]*?)|\{\s*([^;}]*?)\s*\})\s*;")
    return [(text.count("\n", 0, m.start()) + 1, (m.group(1) or m.group(2) or "").strip())
            for m in pattern.finditer(text)]


def _definitions(file: Path, name: str) -> list[tuple[int, str]]:
    key = (file, file.stat().st_mtime_ns, name)
    if key not in _constant_cache:
        _constant_cache[key] = constant_definitions(file.read_text(encoding="utf-8", errors="replace"), name)
    return _constant_cache[key]


def read_constant(root: Path, name: str) -> tuple[int, Path, int]:
    """Value, file and line of the integer constant `name` (NAME of scope::NAME), defined exactly once in the C++
    files under `root` (or in the file `root`). ValueError with the reason otherwise (no definition, several, no
    integer literal)."""
    short = name.rsplit("::", 1)[-1]
    files = [Path(root)] if Path(root).is_file() else sorted(Path(root).rglob("*"))
    found = [(file, line, init) for file in files
             if file.suffix in CPP_SUFFIXES and file.is_file() for line, init in _definitions(file, short)]
    if not found:
        raise ValueError(f"no definition of {name} in {root}")
    if len(found) > 1:
        raise ValueError(f"{name} is defined {len(found)} times in {root} ("
                         + ", ".join(f"{f.name}:{line}" for f, line, _ in found) + "), expected one definition")
    file, line, init = found[0]
    match = _INTEGER_RE.fullmatch(init)
    if match is None:
        raise ValueError(f"{name} in {file.name}:{line} is '{init}', not an integer literal")
    return int(match.group(1), 0), file, line


@dataclass(frozen=True)
class NameBound:
    limit: NameLimit
    chars: int                  # longest name the code takes
    file: Path                  # definition of the constant
    line: int

    def source(self) -> str:
        try:
            where = self.file.resolve().relative_to(REPO).as_posix()
        except ValueError:
            where = str(self.file)
        return f"{self.limit.constant} in {where}:{self.line}"


def name_bounds(family: str, repo: Path = REPO, limits: dict[str, NameLimit] | None = None
                ) -> tuple[dict[str, NameBound], list[Message]]:
    """Limits of NAME_LIMITS for a family, read from the code; errors for definitions that cannot be read."""
    bounds: dict[str, NameBound] = {}
    messages: list[Message] = []
    for kind, limit in (NAME_LIMITS if limits is None else limits).items():
        root = Path(repo) / limit.root.format(family=family)
        try:
            if not root.is_dir():
                raise ValueError(f"no definition of {limit.constant}: directory {root} is missing")
            chars, file, line = read_constant(root, limit.constant)
        except ValueError as e:
            if not limit.required and str(e).startswith("no definition"):
                continue                        # the family port has no such names in its log lines
            messages.append(Message("error", f"longest {limit.what} unknown (tools/nxconfig/checks.py "
                                    f"NAME_LIMITS): {e}", root))
            continue
        bounds[kind] = NameBound(limit, chars, file, line)
    return bounds, messages


def name_lengths(cfg: Config, rep: Reporter, bounds: dict[str, NameBound] | None = None):
    """Names of the configuration longer than the code takes (ARCH-43), reported on the entry that sets the name.

    CAN buses the project uses (board can[].name, can[].instance) and the labels of the used instances of
    LABELED_MODULES (board devices[].name, project modules.<device>.console). Family neutral: the limits of the
    family port are read from platforms/<family>/port.
    """
    if bounds is None:
        bounds, messages = name_bounds(cfg.derivative.derivative.family)
        rep.messages.extend(messages)

    def check(kind: str, text: str, level: str, path: tuple, what: str):
        bound = bounds.get(kind)
        if bound is not None and len(text) > bound.chars:
            rep.error(level, path, f"{what} has {len(text)} characters, the code takes at most {bound.chars} "
                      f"({bound.source()}): the log lines are sized for it, the build would stop at a static_assert")

    index_of = {bus.name: j for j, bus in enumerate(cfg.board.can)}
    for bus in cfg.selected("can"):
        j, ident = index_of[bus.name], c_name(bus.name)
        check("can_bus", ident, "board", ("can", j, "name"),
              f"name of CAN bus {bus.name}" + (f" (in the code {ident})" if ident != bus.name else ""))
        check("can_controller", bus.instance, "board", ("can", j, "instance"),
              f"controller name {bus.instance} of CAN bus {bus.name}")

    if cfg.integration is None:
        return
    device_index = {dev.name: i for i, dev in enumerate(cfg.board.devices)}
    for dev in cfg.integration.devices:
        if dev.module not in LABELED_MODULES:
            continue
        kind, source = LABELED_MODULES[dev.module]
        if source == "device":
            check(kind, dev.name, "board", ("devices", device_index[dev.name], "name"),
                  f"label of {dev.name} ({dev.module}, its device name)")
        else:
            console = str(dev.parameters.get(CONSOLE_PARAMETER, ""))
            given = cfg.project.modules.get(dev.name, {})
            path = ("modules", dev.name) + ((CONSOLE_PARAMETER,) if CONSOLE_PARAMETER in given else ())
            check(kind, console.upper(), "project", path,
                  f"label {console.upper()} of {dev.name} ({dev.module}, console group {console} in upper case)")


# ----------------------------------------------------------------------------------------------- safety timing
# SYS-25 (after SYS-23 and SYS-40): the times of the safety task are module parameters of the family's safety
# package (platforms/<family>/port/safety/safeLifecycle/module.yaml, project.yaml modules.safeLifecycle):
#
#     task_period_ms        period of the safety task (SafetySystem, SafetyManager::CYCLE_PERIOD_MS)
#     watchdog_timeout_ms   time-out of the MCU watchdog (SafeWatchdog::WATCHDOG_TIMEOUT_MS)
#     cycle_lateness_ms     tolerated lateness of a cycle start of the period check (SafetyManager, SM-04)
#
# Derived here, never set:
# - SYS-53: the limits of the low power oscillator relative to nominal, in per mille and rounded outwards (slowest
#   floor(min_hz x 1000 / nominal_hz), fastest ceil(max_hz x 1000 / nominal_hz)), from the derivative database
#   (mcudb clock.lpo, data sheet) - before, constants of the family header LptmrCycleTimer.h. The code takes the
#   same numbers (NX_MODULE_SAFELIFECYCLE_DERIVED_LPO_*_PER_MILLE), so this file and the static_asserts of
#   SafetyManager.cpp compute alike.
# - the watchdog service interval: the most cycles that still leave three services within the time-out (finding F2
#   of doc/safety/concept.md, section 7.1). SYS-53, decision `sys25-lpo` of the user (2026-09-22): at the fastest
#   LPO of the data sheet. The driver loads the counter for the nominal LPO, so a watchdog on the LPO
#   (WATCHDOG_COUNTERS lpo) expires after time-out x 1000 / lpo_max_per_mille at the earliest:
#
#     3 x service cycles x period x lpo_max_per_mille <= time-out x 1000
#     demo 3 x 7 x 10 ms x 1086 = 228060 <= 250000       (8 cycles, the nominal derivation: 260640)
#
#   A family whose watchdog is not on the LPO calculates with the nominal clock (1000 per mille).
#
# The derivation holds the relation by construction; the SYS-23 comparison stays as a counter-check (a changed
# derivation fails it), and SafetyManager.cpp asserts the same relation on the generated values at compile time.
# Checked as well, each with the YAML path of the value to change:
# - the time-out against the watchdog counter of the family (WATCHDOG_COUNTERS: S32K1 WDOG at most 511 ms);
# - the conditions of the static_asserts of the period check in SafetyManager.cpp (SYS-40), with the tick and the
#   width of the family's period-check timer (PERIOD_CHECK_TIMER, read from its header) and the LPO limits, so a
#   project learns it in the configure step, not from the compiler;
# - SYS-53, module parameters of the safe supervisor (platforms/<family>/port/safety/safeSupervisor/module.yaml):
#   the window of the device-bus correlation of SYS-29 is a time, device_bus_window_ms; derived is its length in
#   safety cycles, ceil(window / period) - never shorter than the time set (demo 200 ms / 10 ms = 20 cycles).
#   Warnings: window shorter than two supervision cycles of the slowest device on a bus, more device buses than the
#   slots of the supervisor (DEVICE_BUS_CORRELATION). Error: fault-free time of the reset-loop limit (SYS-43) shorter
#   than one period (the static_assert in the family's main.cpp).
# A family without the module (no safety package of its own, e.g. stm32 today) has nothing to check. The code
# files read for the constants are configure inputs (Config.code_inputs, NX_CONFIG_FILES).

SAFETY_MODULE = "safeLifecycle"                     # module of the family port that owns the safety task
PERIOD, TIMEOUT, LATENESS = "task_period_ms", "watchdog_timeout_ms", "cycle_lateness_ms"
SERVICE_CYCLES = "watchdog_service_cycles"          # derived: NX_MODULE_SAFELIFECYCLE_DERIVED_WATCHDOG_SERVICE_CYCLES
LPO_MIN, LPO_MAX = "lpo_min_per_mille", "lpo_max_per_mille"     # derived: NX_MODULE_SAFELIFECYCLE_DERIVED_LPO_*
WATCHDOG_SERVICES = 3                               # service opportunities before the time-out (concept 7.1)
PER_MILLE = 1000

SUPERVISOR_MODULE = "safeSupervisor"                # module parameters of the safe supervisor (SYS-53)
BUS_WINDOW = "device_bus_window_ms"
BUS_WINDOW_CYCLES = "device_bus_window_cycles"      # derived: NX_MODULE_SAFESUPERVISOR_DERIVED_DEVICE_BUS_WINDOW_CYCLES
CLEAR_MS = "reset_loop_clear_ms"


@dataclass(frozen=True)
class WatchdogCounter:
    """Time-out counter of the MCU watchdog of a family, as its driver loads it."""

    clock_hz: int               # nominal counter clock the driver assumes
    counter_max: int            # largest counter value
    source: str                 # for the messages
    lpo: bool = False           # SYS-53: counts the low power oscillator (mcudb clock.lpo) - shortest at its fastest


WATCHDOG_COUNTERS = {
    # OpenBSW safeBspMcuWatchdog (s32k1xx): TOVAL = (128000 / 1000) x time-out in ms, WDOG clocked by the LPO
    # (CS[CLK] = 1, nominal 128 kHz); TOVAL is 16 bit ([RM13] WDOG chapter), so 65535 / 128 = 511 ms at most.
    # A longer time-out would be truncated by the register and fail the driver's own configuration check.
    "s32k1xx": WatchdogCounter(128000, 0xFFFF, "S32K1 WDOG: TOVAL 16 bit at the nominal 128 kHz LPO", lpo=True),
}


@dataclass(frozen=True)
class TimingConstant:
    """An integer constant of the C++ code the safety timing is checked against."""

    what: str                   # for the messages
    constant: str               # scope::NAME as the messages show it; the definition is found by NAME
    root: str                   # file or directory relative to the repository ({family}: family of the derivative)
    #                             that holds exactly one definition of the constant
    required: bool = True       # False: a family port without the definition has no such mechanism to check


# SYS-40: independent time base of the period check of the family (S32K1: LPTMR on LPO1K). Tick and counter width
# are facts of the timer driver and read from its header; the tolerance of its clock is the LPO range of the
# derivative (mcudb clock.lpo, SYS-53).
PERIOD_CHECK_TIMER = {
    "hz": TimingConstant("nominal tick of the period-check timer", "supervision::CYCLE_TIMER_NOMINAL_HZ",
                         "platforms/{family}/port/safety/safeSupervisionCheck/include", required=False),
    "mask": TimingConstant("counter mask of the period-check timer", "supervision::CYCLE_TIMER_MASK",
                           "platforms/{family}/port/safety/safeSupervisionCheck/include", required=False),
}

# SYS-29 (F5): device faults of several devices on one bus within a window are one "bus lost". The window is a module
# parameter since SYS-53 (device_bus_window_ms); the number of buses tracked at the same time is a code constant.
DEVICE_BUS_CORRELATION = {
    "slots": TimingConstant("buses the device-bus correlation tracks", "SafeSupervisor::DEVICE_BUS_SLOTS",
                            "ecu/safety/safeSupervisor/include", required=False),
}
DEVICE_CYCLE_PARAMETER = "cycle_ms"                 # supervision period of a device module (module.yaml)


@dataclass(frozen=True)
class SafetyTiming:
    """Checked times of the safety task and what is derived from them."""

    period_ms: int
    timeout_ms: int
    lateness_ms: int
    service_cycles: int                             # derived: see service_cycles()
    interval_max_ticks: int | None = None           # bounds of the period check (None: family without it)
    window_min_ticks: int | None = None
    lpo_min_per_mille: int = PER_MILLE              # derived from mcudb clock.lpo (nominal: 1000, 1000)
    lpo_max_per_mille: int = PER_MILLE

    @property
    def service_interval_ms(self) -> int:
        return self.service_cycles * self.period_ms


def lpo_per_mille(lpo) -> tuple[int, int]:
    """(slowest, fastest) LPO of the data sheet relative to nominal in per mille, rounded outwards - so a healthy LPO
    anywhere in the range never trips a check derived from them (mcudb clock.lpo, model.LowPowerOscillator)."""
    return (lpo.min_hz * PER_MILLE) // lpo.nominal_hz, -((-lpo.max_hz * PER_MILLE) // lpo.nominal_hz)


def service_cycles(period_ms: int, timeout_ms: int, max_per_mille: int = PER_MILLE) -> int:
    """Safety cycles per watchdog service: the most that still leave three services within the time-out, at the
    fastest counter clock (`max_per_mille` of nominal; SYS-53 the fastest LPO)."""
    return (timeout_ms * PER_MILLE) // (WATCHDOG_SERVICES * period_ms * max_per_mille)


def period_check_bounds(period_ms: int, lateness_ms: int, window: int, hz: int, min_per_mille: int,
                        max_per_mille: int) -> tuple[int, int]:
    """(largest interval that passes, smallest window) in timer ticks - the formulas of SafetyManager.cpp."""
    interval_max = ((period_ms + lateness_ms) * hz * max_per_mille) // 1000000 + 1
    window_min = ((window * period_ms - lateness_ms) * hz * min_per_mille) // 1000000
    return interval_max, window_min


def _repo_relative(file: Path, repo: Path) -> str:
    try:
        return file.resolve().relative_to(Path(repo).resolve()).as_posix()
    except ValueError:
        return str(file)


def read_constants(table: dict[str, TimingConstant], family: str, repo: Path = REPO
                   ) -> tuple[dict[str, int] | None, list[Path], str | None]:
    """Values of a table of code constants: (values, files read, problem). values None and no problem: the family
    has none of the optional constants (no such mechanism); a problem names the constant that cannot be read."""
    values: dict[str, int] = {}
    files: list[Path] = []
    for key, const in table.items():
        root = Path(repo) / const.root.format(family=family)
        try:
            if not root.exists():
                raise ValueError(f"no definition of {const.constant}: {_repo_relative(root, repo)} is missing")
            value, file, _ = read_constant(root, const.constant)
        except ValueError as e:
            if not const.required and not values and str(e).startswith("no definition"):
                return None, files, None
            return None, files, f"{const.what} unknown: {e}"
        values[key] = value
        files.append(file)
    return values, files, None


def _shortest_timeout(timeout: int, max_per_mille: int) -> str:
    return f"{timeout * PER_MILLE / max_per_mille:.1f}".rstrip("0").rstrip(".")


def watchdog_timing_messages(period: int, timeout: int, cycles: int, values: str = "",
                             max_per_mille: int = PER_MILLE) -> list[str]:
    """SYS-23 relation (finding F2): 3 x service interval <= watchdog time-out, for `cycles` safety cycles per
    service; SYS-53: at the fastest counter clock (`max_per_mille` of nominal), where the time-out is shortest. Texts
    of the violations: no service interval possible at all (cycles < 1), or a derivation whose three service
    intervals exceed the time-out - the counter-check of service_cycles()."""
    fastest = "" if max_per_mille == PER_MILLE else (
        f" - at the fastest LPO of the data sheet ({max_per_mille} per mille of nominal) it expires after "
        f"{_shortest_timeout(timeout, max_per_mille)} ms")
    if cycles < 1:
        return [f"MCU watchdog time-out {timeout} ms{fastest} is shorter than {WATCHDOG_SERVICES} periods of the "
                f"safety task ({WATCHDOG_SERVICES} x {period} ms = {WATCHDOG_SERVICES * period} ms): the task cannot "
                f"service the watchdog {WATCHDOG_SERVICES} times before it expires, a single late service would reset "
                f"the ECU (finding F2 of doc/safety/concept.md). Raise {TIMEOUT} or shorten {PERIOD}"]
    needed = WATCHDOG_SERVICES * cycles * period
    if needed * max_per_mille > timeout * PER_MILLE:
        return [f"MCU watchdog time-out {timeout} ms{fastest} is shorter than {WATCHDOG_SERVICES} service intervals "
                f"of the safety task ({WATCHDOG_SERVICES} x {cycles} cycles x {period} ms = {needed} ms): a single "
                f"late service would reset the ECU. Required: {WATCHDOG_SERVICES} x service interval <= time-out "
                f"(finding F2 of doc/safety/concept.md)" + (f". Values: {values}" if values else "")]
    return []


def safety_timing(cfg: Config, rep: Reporter, repo: Path = REPO) -> SafetyTiming | None:
    """SYS-25: times of the safety task (module parameters of safeLifecycle), derived service interval and checks.

    Findings go to `rep` with the YAML path modules.safeLifecycle.<parameter> of the project; the derived values
    (service cycles, LPO limits; SYS-53 the device-bus window of safeSupervisor) are handed to the generator as
    derived values of the modules (ModuleUse.derived). The code files read for constants are appended to
    cfg.code_inputs. None if the project has no such module or a value is not usable.
    """
    use = cfg.integration.modules.get(SAFETY_MODULE) if cfg.integration is not None else None
    if use is None:
        supervisor = cfg.integration.modules.get(SUPERVISOR_MODULE) if cfg.integration is not None else None
        # a safeLifecycle with a wrong value is missing from the integration too - reported there already
        if supervisor is not None and SAFETY_MODULE not in cfg.modules.manifests:
            rep.error("project", ("modules", SUPERVISOR_MODULE),
                      f"module {SUPERVISOR_MODULE} counts its times in cycles of the safety task, but the project has "
                      f"no {SAFETY_MODULE} module (platforms/<family>/port/safety/{SAFETY_MODULE}/module.yaml)")
        return None
    family = cfg.derivative.derivative.family
    period, timeout, lateness = use.parameters[PERIOD], use.parameters[TIMEOUT], use.parameters[LATENESS]
    start = len(rep.messages)

    def path(parameter: str) -> tuple:
        return ("modules", SAFETY_MODULE, parameter)

    values = (f"{PERIOD} {period} ms, {TIMEOUT} {timeout} ms, {LATENESS} {lateness} ms "
              f"(project.yaml modules.{SAFETY_MODULE}, defaults of platforms/{family}/port/safety/{SAFETY_MODULE}/"
              f"module.yaml)")
    # SYS-53: LPO limits of the derivative (data sheet), needed by an LPO watchdog and by the period check
    counter = WATCHDOG_COUNTERS.get(family)
    if cfg.derivative.clock is None:
        return None                 # no clock data at all: reported by the clock checks, nothing to derive from
    lpo = cfg.derivative.clock.lpo
    limits = lpo_per_mille(lpo) if lpo is not None else None
    derivative = cfg.derivative.derivative.name
    if limits is None and counter is not None and counter.lpo:
        rep.error("derivative", ("clock",),
                  f"derivative {derivative} has no LPO range (clock.lpo: nominal_hz, min_hz, max_hz with the data "
                  f"sheet as source): the watchdog of family {family} counts the LPO, and its service interval is "
                  f"derived at the fastest LPO (SYS-53)")
        return None
    # decision sys25-lpo: a watchdog on the LPO is serviced for its fastest frequency, others for their nominal clock
    wdg_per_mille = limits[1] if counter is not None and counter.lpo else PER_MILLE

    cycles = service_cycles(period, timeout, wdg_per_mille)
    for text in watchdog_timing_messages(period, timeout, cycles, values, wdg_per_mille):
        rep.error("project", path(TIMEOUT), text)
    if counter is not None and timeout * (counter.clock_hz // 1000) > counter.counter_max:
        limit = counter.counter_max // (counter.clock_hz // 1000)
        rep.error("project", path(TIMEOUT),
                  f"MCU watchdog time-out {timeout} ms does not fit the watchdog of family {family}: at most {limit} ms "
                  f"({counter.source}; tools/nxconfig/checks.py WATCHDOG_COUNTERS)")

    bounds: tuple[int, int] | None = None
    timer, files, problem = read_constants(PERIOD_CHECK_TIMER, family, repo)
    cfg.code_inputs.extend(f for f in files if f not in cfg.code_inputs)
    if problem is not None:
        rep.error("project", path(PERIOD), f"period check of the safety task cannot be checked, {problem} "
                  "(tools/nxconfig/checks.py PERIOD_CHECK_TIMER)")
    elif timer is not None and limits is None:
        rep.error("derivative", ("clock",),
                  f"derivative {derivative} has no LPO range (clock.lpo): the period check of the safety task of "
                  f"family {family} derives its bounds from it (SYS-40, SYS-53)")
    elif timer is not None and cycles >= 1 and lateness < period:
        bounds = period_check_bounds(period, lateness, cycles, timer["hz"], *limits)
        interval_max, window_min = bounds
        # the static_asserts of SafetyManager.cpp (SYS-40), in the same order
        broken = []
        if window_min <= 0:
            broken.append(f"the window of {cycles} cycles spans no tick of the timer ({window_min} ticks)")
        elif interval_max >= window_min:
            broken.append(f"one interval (at most {interval_max} ticks) is not shorter than the window of {cycles} "
                          f"cycles (at least {window_min} ticks) - the window needs more cycles per watchdog service, "
                          f"i.e. a longer {TIMEOUT}")
        if cycles * interval_max >= timer["mask"]:
            broken.append(f"a window of {cycles} cycles x {interval_max} ticks does not fit into one wrap-around of "
                          f"the timer (mask {timer['mask']:#x})")
        for text in broken:
            rep.error("project", path(PERIOD),
                      f"period check of the safety task (SM-04) cannot work: {text}. Window = watchdog service "
                      f"interval of {cycles} cycles; {values}; LPO {limits[0]}..{limits[1]} per mille of nominal "
                      f"(mcudb {derivative} clock.lpo); the static_assert in platforms/{family}/port/safety/"
                      f"{SAFETY_MODULE}/src/{SAFETY_MODULE}/SafetyManager.cpp would stop the build")

    _safe_supervisor(cfg, rep, period, repo)
    if len([m for m in rep.messages[start:] if m.severity == "error"]):
        return None
    if limits is not None:
        use.derived[LPO_MIN] = (limits[0], f"floor(clock.lpo.min_hz x 1000 / nominal_hz) = floor({lpo.min_hz * 1000} / "
                                           f"{lpo.nominal_hz}) (mcudb {derivative}, slowest LPO)")
        use.derived[LPO_MAX] = (limits[1], f"ceil(clock.lpo.max_hz x 1000 / nominal_hz) = ceil({lpo.max_hz * 1000} / "
                                           f"{lpo.nominal_hz}) (mcudb {derivative}, fastest LPO)")
    if wdg_per_mille == PER_MILLE:
        how = f"floor({TIMEOUT} / ({WATCHDOG_SERVICES} x {PERIOD})) = floor({timeout} / {WATCHDOG_SERVICES * period})"
    else:
        how = (f"floor({TIMEOUT} x 1000 / ({WATCHDOG_SERVICES} x {PERIOD} x {LPO_MAX})) = floor({timeout * PER_MILLE} "
               f"/ {WATCHDOG_SERVICES * period * wdg_per_mille}) (fastest LPO: time-out "
               f"{_shortest_timeout(timeout, wdg_per_mille)} ms)")
    use.derived[SERVICE_CYCLES] = (cycles, how)
    return SafetyTiming(period, timeout, lateness, cycles, *(bounds or (None, None)),
                        *(limits or (PER_MILLE, PER_MILLE)))


def _safe_supervisor(cfg: Config, rep: Reporter, period: int, repo: Path):
    """SYS-53: module parameters of the safe supervisor that count safety cycles. Derived: the window of the
    device-bus correlation (SYS-29, F5) in cycles; warnings for it (a window shorter than two supervision cycles of
    the slowest device on an SPI bus, more device buses than slots); error: a fault-free time of the reset-loop limit
    (SYS-43) shorter than one period."""
    use = cfg.integration.modules.get(SUPERVISOR_MODULE)
    if use is None:
        return
    window_ms, clear_ms = use.parameters[BUS_WINDOW], use.parameters[CLEAR_MS]
    if clear_ms < period:
        rep.error("project", ("modules", SUPERVISOR_MODULE, CLEAR_MS),
                  f"fault-free time of the reset-loop limit {clear_ms} ms is shorter than one period of the safety "
                  f"task ({PERIOD} {period} ms): it is counted in safety cycles, so it has to span one at least "
                  f"(static_assert in platforms/{cfg.derivative.derivative.family}/port/main/src/main.cpp)")
    cycles = -(-window_ms // period)
    use.derived[BUS_WINDOW_CYCLES] = (cycles, f"ceil({BUS_WINDOW} / {SAFETY_MODULE}.{PERIOD}) = "
                                              f"ceil({window_ms} / {period})")
    window = cycles * period
    on_bus = [d for d in cfg.integration.devices if d.spi]
    cycled = [(d.parameters[DEVICE_CYCLE_PARAMETER], d.name) for d in on_bus
              if isinstance(d.parameters.get(DEVICE_CYCLE_PARAMETER), int)]
    if cycled:
        slowest, name = max(cycled)
        if window < 2 * slowest:
            rep.warning("project", ("modules", SUPERVISOR_MODULE, BUS_WINDOW),
                        f"device-bus correlation of the safety supervisor (SYS-29): its window of {window_ms} ms "
                        f"({cycles} safety cycles of {period} ms = {window} ms) is shorter than two supervision "
                        f"cycles of the slowest device on a bus ({name}, {DEVICE_CYCLE_PARAMETER} {slowest} ms) - "
                        f"communication faults of two devices on one bus may be taken as independent")
    consts, files, problem = read_constants(DEVICE_BUS_CORRELATION, cfg.derivative.derivative.family, repo)
    cfg.code_inputs.extend(f for f in files if f not in cfg.code_inputs)
    if problem is not None:
        rep.warning("project", ("modules", SUPERVISOR_MODULE), f"device-bus correlation of the safety supervisor "
                    f"cannot be checked, {problem} (tools/nxconfig/checks.py DEVICE_BUS_CORRELATION)")
        return
    if consts is None:
        return
    buses = sorted({b for d in on_bus for b in d.spi})
    if len(buses) > consts["slots"]:
        rep.warning("project", ("use", "spi"),
                    f"device-bus correlation of the safety supervisor (SYS-29) tracks {consts['slots']} buses at the "
                    f"same time, the project has devices on {len(buses)} ({', '.join(buses)}): faults on a further "
                    f"bus are reported without correlation (SafeSupervisor::DEVICE_BUS_SLOTS)")


# --------------------------------------------------------------------- requirements of the family port (PLAT-95)
# PLAT-95 (finding of TOOL-59): the hand-written family port `platforms/<family>/port` (PLAT-23) is compiled into
# every project of its family (root CMakeLists.txt, NX_FAMILY_PORT_DIR). Its code reads a few configured names that
# no schema asks for - for s32k1xx the internal ADC channels of the safety supervision (Ai_BANDGAP, Ai_ADCINTERNAL0)
# and the pins of the safe state. A project without them failed in the compiler, deep in the generated headers, and
# nxconfig said nothing. Since a new ECU almost always brings its own board instead of a known eval board (TOOL-72),
# the port states its requirements in its manifest `platforms/<family>/port/port.yaml`, and they are checked here
# with file, line, YAML path and what to do.
#
# The manifest is a property of the port, not a fourth configuration level (architecture section 4): it carries no
# value of the ECU. Which ADC instance offers the bandgap stays a derivative fact, which pin shows the safe state a
# project decision, the reference voltage a board fact - the manifest only says *that* the port needs such an entry
# and *why*. A family without a port template, or a port without a manifest, has nothing to check.
#
# Checked for a project that is **built** with the port: its family has a port template `platforms/<family>/port`
# and its board is a real board of the workspace or of the tree (built_with_family_port below says why both).
# ARCH-58 changed the marker: until then it was the board port `ecu/platforms/<board>/Options.cmake`, which the
# root CMakeLists.txt demanded of every board; the feature switches are derived since then and that file is
# optional. A configuration-only profile (the test profiles of tools/nxconfig and tools/bswgen, which keep their
# boards next to their tests) meets neither condition: nothing is compiled from it, so no requirement can fail a
# build. The manifest itself is read and checked for every project of the family, so a broken one is never
# silently ignored, and it is a configure input (NX_CONFIG_FILES) like the manifests of the port modules.
#
# A port states only requirements that hold for every project it is built into. No port demands a named CAN bus
# today: the s32k1xx port used to wire one bus by hand and was the reason this stayed out of its manifest (the
# header was compiled only with PLATFORM_SUPPORT_CAN, and a project may use no CAN bus at all, ARCH-42), but
# ARCH-61 made the wiring generic over every used bus, whatever it is called. The group `can` stays available for
# a port that does come to demand a named bus on a named instance; nxconfig would then tell whether it applies.

PORT_MANIFEST = "port.yaml"                         # platforms/<family>/port/port.yaml
PORT_SCHEMA_VERSION = 1
# Requirement groups of the manifest: group -> keys that name the required entry. Every entry may also carry the
# notes `reason` (why the port needs it, quoted in the message) and `code` (the file of the port that reads it).
PORT_GROUPS = {"adc_internal": ("name", "channel"), "can": ("name", "instance"),
               "board": ("key",), "project": ("key",)}
PORT_NOTES = ("reason", "code")


@dataclass(frozen=True)
class PortRequirements:
    """What a family port template needs from project, board and derivative (platforms/<family>/port/port.yaml)."""

    file: Path
    where: str                                      # the file as the messages name it (relative to the tree)
    port: str                                       # the family the manifest belongs to
    groups: dict[str, list[dict]] = field(default_factory=dict)

    def entries(self, group: str) -> list[dict]:
        return self.groups.get(group, [])

    def note(self, entry: dict) -> str:
        """`reason` and `code` of one requirement, for the end of a message."""
        text = f" - {entry['reason']}" if entry.get("reason") else ""
        text += f" ({entry['code']})" if entry.get("code") else ""
        return f"{text}; stated in {self.where}"


def port_manifest_file(family: str, repo: Path = REPO) -> Path:
    """The manifest of the family port template, whether it exists or not."""
    return Path(repo) / "platforms" / family / "port" / PORT_MANIFEST


def built_with_family_port(cfg: Config, repo: Path = REPO) -> bool:
    """True if a project on this board is compiled with the family port. Two conditions, both checkable:

    1. the family of the derivative has a port template `platforms/<family>/port` - without it the root
       CMakeLists.txt aborts with the list of its missing parts (rm4x, which has none yet), so nothing is ever
       compiled;
    2. the board is a **real board**: its `board.yaml` lies in a `boards/` folder of the workspace or of the tree
       (`workspace.board_dirs()`, ARCH-56). The test profiles of tools/nxconfig and tools/bswgen keep their boards
       next to their tests instead, and they are never built.

    ARCH-58 replaced the former marker `ecu/platforms/<board>/Options.cmake`, which the root CMakeLists.txt
    demanded of every buildable board. Since the feature switches are derived, that file is optional - a board
    without it builds - so its absence no longer says "this profile is never compiled", and a customer board that
    brings no board port at all would otherwise escape the check of PLAT-95 exactly where N5 is meant to help.
    """
    if not features.family_port_template(cfg.derivative.derivative.family, repo).is_file():
        return False
    if cfg.workspace is not None:
        roots = cfg.workspace.board_dirs()
    else:
        roots = (Path(repo) / "boards",)
    parent = cfg.board_file.parent.parent
    return any(_same_dir(parent, root) for root in roots)


def _same_dir(a: Path, b: Path) -> bool:
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return False


def read_port_manifest(file: Path, family: str, repo: Path = REPO
                       ) -> tuple[PortRequirements | None, list[Message]]:
    """Reads and checks `platforms/<family>/port/port.yaml`. None with messages if it cannot be used.

    Hand-written instead of a pydantic model: the manifest belongs to the port, not to the three configuration
    levels, and its vocabulary is what this file can act on (PORT_GROUPS) - a requirement nxconfig could not check
    would be a promise it does not keep, so it is rejected here with its YAML path.
    """
    messages: list[Message] = []
    tree = None

    def error(path, text):
        messages.append(Message("error", text, file, tuple(path), line_of(tree, tuple(path))))

    try:
        tree = YAML(typ="rt").load(file.read_text(encoding="utf-8"))
    except (OSError, YAMLError) as e:
        messages.append(Message("error", f"cannot read the port manifest: {e}", file))
        return None, messages
    if not isinstance(tree, dict):
        error((), "expected a mapping at the top level of the port manifest")
        return None, messages
    version = tree.get("schema_version")
    if version != PORT_SCHEMA_VERSION:
        error(("schema_version",), f"port manifest schema_version {version!r}, expected {PORT_SCHEMA_VERSION}")
        return None, messages
    if tree.get("port") != family:
        error(("port",), f"port manifest says port: {tree.get('port')!r}, but it lies in the port of family "
                         f"{family}: a port manifest belongs to the family whose folder it is in")
        return None, messages
    requires = tree.get("requires", {})
    if not isinstance(requires, dict):
        error(("requires",), "`requires:` of the port manifest must be a mapping of requirement groups "
                             f"({', '.join(PORT_GROUPS)})")
        return None, messages

    groups: dict[str, list[dict]] = {}
    ok = True
    for group, entries in requires.items():
        if group not in PORT_GROUPS:
            error(("requires", group), f"unknown requirement group {group!r} (known: {', '.join(PORT_GROUPS)})")
            ok = False
            continue
        if not isinstance(entries, list):
            error(("requires", group), f"requirement group {group} must be a list of entries")
            ok = False
            continue
        keys = PORT_GROUPS[group]
        checked = []
        for i, entry in enumerate(entries):
            where = ("requires", group, i)
            if not isinstance(entry, dict):
                error(where, f"entry {i} of requirement group {group} must be a mapping ({', '.join(keys)})")
                ok = False
                continue
            bad = False
            for key in keys:
                value = entry.get(key)
                if not isinstance(value, str) or not value.strip():
                    error(where + (key,), f"requirement group {group} needs `{key}:` with a non-empty name")
                    ok, bad = False, True
            for key in entry:
                if key not in keys and key not in PORT_NOTES:
                    error(where + (key,), f"unknown key {key!r} of requirement group {group} "
                                          f"(known: {', '.join(keys + PORT_NOTES)})")
                    ok, bad = False, True
            if not bad:
                checked.append({k: str(v).strip() for k, v in entry.items()})
        groups[group] = checked
    if not ok:
        return None, messages
    return PortRequirements(file, _repo_relative(file, repo), family, groups), messages


def port_requirements(cfg: Config, rep: Reporter, repo: Path = REPO) -> PortRequirements | None:
    """PLAT-95: the requirements of the family port against project, board and derivative.

    Returns the manifest that was read (None: no family port manifest, or an unusable one).
    """
    family = cfg.derivative.derivative.family
    file = port_manifest_file(family, repo)
    if not file.is_file():
        return None                                 # family without a port template, or a port without a manifest
    requirements, messages = read_port_manifest(file, family, repo)
    rep.messages.extend(messages)
    if requirements is None:
        return None
    if file not in cfg.code_inputs:
        cfg.code_inputs.append(file)                # a changed requirement has to reach the configure step
    if not built_with_family_port(cfg, repo):
        return requirements                         # configuration-only board profile: see the section comment
    _port_adc_internal(cfg, rep, requirements, family)
    _port_can(cfg, rep, requirements, family)
    _port_values(cfg, rep, requirements, family)
    return requirements


def _port_adc_internal(cfg: Config, rep: Reporter, requirements: PortRequirements, family: str):
    """Internal ADC inputs the port reads by name (project.yaml adc_internal)."""
    have = {ch.name: (i, ch) for i, ch in enumerate(cfg.project.adc_internal)}
    for want in requirements.entries("adc_internal"):
        name, channel, note = want["name"], want["channel"], requirements.note(want)
        found = have.get(name)
        if found is not None:
            index, ch = found
            if ch.channel != channel:
                rep.error("project", ("adc_internal", index, "channel"),
                          f"adc_internal {name} reads the internal channel {ch.channel}, the family port {family} "
                          f"needs {channel} behind that name (its code reads it as "
                          f"bios::AnalogInput::Ai_{name}){note}")
            continue
        offers = sorted(n for n, x in cfg.derivative.instances.items()
                        if x.class_ == "adc" and channel in (x.internal_channels or []))
        if offers:
            rep.error("project", ("adc_internal",),
                      f"the family port {family} reads the internal ADC channel {name}, the project has no "
                      f"adc_internal entry of that name: add "
                      f"`- {{ name: {name}, instance: {offers[0]}, channel: {channel} }}` to adc_internal "
                      f"({cfg.derivative.derivative.name} offers {channel} on {', '.join(offers)}){note}")
        else:
            rep.error("derivative", ("instances",),
                      f"the family port {family} reads the internal ADC channel {channel} (as adc_internal {name}), "
                      f"but derivative {cfg.derivative.derivative.name} has no ADC instance that offers it "
                      f"(instances.<adc>.internal_channels): this derivative cannot carry this port{note}")


def _port_can(cfg: Config, rep: Reporter, requirements: PortRequirements, family: str):
    """CAN buses the port names in its own code (board.yaml can[], project.yaml use.can)."""
    for want in requirements.entries("can"):
        name, instance, note = want["name"], want["instance"], requirements.note(want)
        index = next((i for i, b in enumerate(cfg.board.can) if b.name == name), None)
        if index is None:
            rep.error("board", ("can",),
                      f"the family port {family} wires the CAN bus {name} by hand, board {cfg.board.board.name} "
                      f"has no bus of that name (has: {', '.join(b.name for b in cfg.board.can) or '-'}){note}")
            continue
        if cfg.board.can[index].instance != instance:
            rep.error("board", ("can", index, "instance"),
                      f"the family port {family} holds the registers of {instance} for the CAN bus {name}, the "
                      f"board puts that bus on {cfg.board.can[index].instance}{note}")
        if not any(b.name == name for b in cfg.selected("can")):
            rep.error("project", ("use", "can"),
                      f"the family port {family} wires the CAN bus {name} by hand, the project does not use it "
                      f"(use.can: {', '.join(b.name for b in cfg.selected('can')) or '-'}){note}")


def _port_values(cfg: Config, rep: Reporter, requirements: PortRequirements, family: str):
    """Single values of the board (board.<key>) and sections of the project (<key>) the port's code needs."""
    for level, root, path in (("board", cfg.board.board, ("board",)), ("project", cfg.project, ())):
        for want in requirements.entries(level):
            key, note = want["key"], requirements.note(want)
            if key not in type(root).model_fields:
                rep.messages.append(Message("error", f"the port manifest requires {level}.{key}, which is no entry "
                                                     f"of the {level} schema (tools/nxconfig/model.py)",
                                            requirements.file, ("requires", level)))
                continue
            value = getattr(root, key, None)
            if value is None or value == [] or value == {}:
                rep.error(level, path + (key,),
                          f"the family port {family} needs {_fmt(path + (key,))} of the {level}, which is not "
                          f"set{note}")


# ----------------------------------------------------------------------------------------------- CAN bus off
# COM-37: can.<bus>.bus_off (model.CanBusOff), the behaviour after bus off of libs/bsw/canSystem
# (can/CanBusOffRecovery.h, COM-35). The schema bounds each value by its C type; here the values against each other.

def can_bus_off_problems(bus_off) -> list[tuple[str, str, str]]:
    """(severity, key, text) for the values of one can.<bus>.bus_off entry that do not fit together."""
    found = []
    holds = bus_off.max_pause_ms > 0
    if holds and bus_off.first_pause_ms == 0:
        found.append(("error", "first_pause_ms",
                      f"first_pause_ms 0 with max_pause_ms {bus_off.max_pause_ms}: a pause of 0 ms never doubles, "
                      "the controller is released in the next supervision cycle after every bus off and the bus "
                      "off repeats as fast as without holding - set first_pause_ms > 0, or max_pause_ms 0 for the "
                      "automatic recovery of the controller"))
    if holds and bus_off.first_pause_ms > bus_off.max_pause_ms:
        found.append(("error", "first_pause_ms",
                      f"first_pause_ms {bus_off.first_pause_ms} is above max_pause_ms {bus_off.max_pause_ms}: the "
                      "pause is limited to max_pause_ms from the first bus off on, so the first value has no "
                      "effect - lower first_pause_ms or raise max_pause_ms"))
    if not holds and "first_pause_ms" in bus_off.model_fields_set:
        found.append(("warning", "first_pause_ms",
                      "first_pause_ms has no effect with max_pause_ms 0 (the controller is not held, it leaves bus "
                      "off by itself)"))
    if holds and bus_off.quiet_ms <= bus_off.max_pause_ms:
        found.append(("warning", "quiet_ms",
                      f"quiet_ms {bus_off.quiet_ms} is not longer than max_pause_ms {bus_off.max_pause_ms}: a "
                      "lasting fault can end its series between two bus offs and is logged again from the start "
                      "- choose quiet_ms above max_pause_ms"))
    return found


def _can_bus_off(cfg: Config, rep: Reporter):
    """COM-37: the bus off values of every CAN bus entry of the project (used or not: a value is wrong either way)."""
    for bus, use in cfg.project.can.items():
        if use.bus_off is None:
            continue
        for severity, key, text in can_bus_off_problems(use.bus_off):
            rep.add(severity, "project", ("can", bus, "bus_off", key), f"CAN bus {bus}: {text}")
